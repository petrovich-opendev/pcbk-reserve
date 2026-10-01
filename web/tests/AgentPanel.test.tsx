// Панель «Агенты» и агент разговора (Д7-R65, Review Focus 1 и 3 Д7б; preflight П9–П12, П14, m13–m16).
// Панель — одна, без рантайма. Chat — настоящий адаптер OpenCode, как в ChatView.test.tsx: сеть — подменённый
// глобальный fetch (шлюз OpenCode и GET /pcbk/api/agents через createApi), поток /event дописывает тест.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { AgentItem } from "../src/agents";
import { createApi } from "../src/api";
import { AgentPanel } from "../src/components/AgentPanel";
import { Chat } from "../src/components/Chat";
import {
  T_AGENT_BROKEN, T_AGENT_LINE, T_AGENT_PENDING, T_AGENTS, T_AGENTS_DOWN, T_AGENTS_EMPTY, T_MY_AGENTS,
} from "../src/texts";

const POMOSHNIK: AgentItem = { name: "pomoshnik", title: "Помощник по данным", purpose: "текущие значения и сводки тегов БДРВ",
                               kind: "builtin", state: "ready" };
const KONSTRUKTOR: AgentItem = { name: "konstruktor", title: "Конструктор агентов",
                                 purpose: "за один разговор собирает вашего агента по узлу", kind: "builtin", state: "ready" };
const BUILTIN = [POMOSHNIK, KONSTRUKTOR];
const MINE: AgentItem = { name: "moy-agent", title: "Мой агент", purpose: "давление пара на узле 3", kind: "own", state: "pending" };
const BROKEN: AgentItem = { name: "slomannyy", title: "Сломанный", purpose: "уровень в баке", kind: "own", state: "broken" };
const ITEMS = [...BUILTIN, MINE, BROKEN];

const button = (name: RegExp | string) => screen.getByRole("button", { name }) as HTMLButtonElement;

test("texts_of_panel", () => {                                                    // строки Д7-R65 дословно
  expect([T_AGENTS, T_MY_AGENTS, T_AGENTS_EMPTY, T_AGENT_PENDING, T_AGENT_BROKEN, T_AGENTS_DOWN, T_AGENT_LINE("Икс")]).toEqual([
    "Агенты", "Мои агенты", "Пока нет — соберите агента вместе с Конструктором", "появится после ответа Конструктора",
    "не загрузился — сообщите преподавателю", "Список агентов недоступен — чат работает с помощником по данным", "Агент: Икс",
  ]);
});

test("panel_disables_pending_and_broken", () => {
  const pick = vi.fn();
  render(<AgentPanel items={ITEMS} current="pomoshnik" onPick={pick} />);
  expect(button(/Мой агент.*появится после ответа Конструктора/).disabled).toBe(true);
  expect(button(/Сломанный.*не загрузился/).disabled).toBe(true);
  fireEvent.click(button(/Мой агент/));
  fireEvent.click(button(/Сломанный/));
  expect(pick).not.toHaveBeenCalled();                                            // неактивные не выбираются
  fireEvent.click(button(/Конструктор агентов/));
  expect(pick).toHaveBeenCalledWith("konstruktor");
  expect(pick).toHaveBeenCalledTimes(1);
});

test("panel_markup_contract", () => {
  // договор с Б4 (П12): section.agents; button[data-agent][data-state][aria-current] с .agent-title и .agent-purpose
  const { container } = render(<AgentPanel items={ITEMS} current="konstruktor" onPick={vi.fn()} />);
  const section = container.querySelector("section.agents")!;
  expect(section.getAttribute("aria-label")).toBe("Агенты");
  const rows = [...section.querySelectorAll("button")];
  expect(rows.map((b) => [b.dataset.agent, b.dataset.state])).toEqual([
    ["pomoshnik", "ready"], ["konstruktor", "ready"], ["moy-agent", "pending"], ["slomannyy", "broken"]]);
  expect([...section.querySelectorAll(".agent-title")].map((s) => s.textContent))
    .toEqual(["Помощник по данным", "Конструктор агентов", "Мой агент", "Сломанный"]);
  expect([...section.querySelectorAll(".agent-purpose")].map((s) => s.textContent)).toEqual(ITEMS.map((a) => a.purpose));
  expect(rows.map((b) => b.getAttribute("aria-current"))).toEqual([null, "true", null, null]);   // подсвечен агент разговора
  expect(rows.map((b) => b.type)).toEqual(["button", "button", "button", "button"]);
  // встроенные — до подзаголовка «Мои агенты», свои — после
  const heading = within(section as HTMLElement).getByText("Мои агенты");
  expect(rows.map((b) => Boolean(heading.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)))
    .toEqual([false, false, true, true]);
  expect(container.querySelector("[style]")).toBeNull();                          // CSP: ни одного style
});

test("panel_empty_own", () => {
  render(<AgentPanel items={BUILTIN} current="pomoshnik" onPick={vi.fn()} />);
  expect(screen.getByText("Мои агенты")).toBeTruthy();
  expect(screen.getByText("Пока нет — соберите агента вместе с Конструктором")).toBeTruthy();
  expect(screen.getAllByRole("button")).toHaveLength(2);
});

test("panel_survives_404", () => {
  const { container } = render(<AgentPanel items={null} current="pomoshnik" onPick={vi.fn()} />);
  expect(screen.getByText("Список агентов недоступен — чат работает с помощником по данным")).toBeTruthy();
  expect(container.querySelectorAll("button")).toHaveLength(0);                   // выбрать нечего — чат с помощником
  expect(screen.queryByRole("status")).toBeNull();                                // строка потока Д6 остаётся единственной
});

test("panel_before_first_answer", () => {
  const { container } = render(<AgentPanel items={undefined} current="pomoshnik" onPick={vi.fn()} />);
  expect(container.querySelector("section.agents")).toBeTruthy();
  expect(container.querySelectorAll("button")).toHaveLength(0);
  expect(screen.queryByText(T_AGENTS_DOWN)).toBeNull();                           // не «недоступен», пока ответа нет
  expect(screen.queryByText(T_AGENTS_EMPTY)).toBeNull();
});

// --- Chat: агент разговора (адаптер настоящий, сеть — подменённый fetch) ---

const SES_K = "ses_konstruktor0123456789abcd";      // разговор с Конструктором
const SES_P = "ses_pomoshnik0123456789abcdef";      // разговор с помощником
const session = (id: string, title = "") =>
  ({ id, slug: "s", projectID: "p", directory: "/work", title, version: "1.18.33", time: { created: 1, updated: 1 } });
const ocJson = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const userMsg = (sid: string, mid: string, agent: string, text: string) => ({
  info: { id: mid, sessionID: sid, role: "user", time: { created: 1 }, agent, model: { providerID: "openrouter", modelID: "m" } },
  parts: [{ id: `prt_${mid}`, sessionID: sid, messageID: mid, type: "text", text }],
});
const assistantMsg = (sid: string, mid: string, parentID: string, agent: string, text: string) => ({
  info: { id: mid, sessionID: sid, role: "assistant", time: { created: 2, completed: 3 }, parentID, modelID: "m",
          providerID: "openrouter", mode: agent, agent, path: { cwd: "/work", root: "/work" }, cost: 0,
          tokens: { input: 1, output: 1, reasoning: 0, cache: { read: 0, write: 0 } }, finish: "stop" },
  parts: [{ id: `prt_${mid}`, sessionID: sid, messageID: mid, type: "text", text }],
});
const HISTORY: Record<string, unknown[]> = {
  [SES_K]: [userMsg(SES_K, "msg_k1", "konstruktor", "собери агента по узлу 3"),
            assistantMsg(SES_K, "msg_k2", "msg_k1", "konstruktor", "Какой узел и какие теги?")],
  [SES_P]: [userMsg(SES_P, "msg_p1", "pomoshnik", "что сейчас на T1"),
            assistantMsg(SES_P, "msg_p2", "msg_p1", "pomoshnik", "T1 = 12,5")],
};

type Answer = Response | Promise<Response> | undefined;

// Двойник шлюза: список разговоров, сессии, история (можно придержать), prompt_async, поток /event и GET
// /pcbk/api/agents (agentCalls — число вызовов). Новый разговор — POST /session с id ses_newN.
// hold(sid, n) — придержать n-ю (с 1) загрузку истории разговора sid до release(sid)
function chatGateway(o: { agents?: () => Answer; list?: () => Answer; hold?: (sid: string, n: number) => boolean;
                          prompt?: (sid: string) => Answer }) {
  const enc = new TextEncoder();
  let ctl: ReadableStreamDefaultController<Uint8Array> | null = null;
  const held = new Map<string, (r: Response) => void>();
  const loads = new Map<string, number>();
  const gw = {
    calls: [] as { method: string; path: string; body: string }[],
    prompts: [] as { session: string; body: Record<string, unknown> }[],
    agentCalls: 0,
    created: 0,
    push(...events: object[]): void { for (const e of events) ctl?.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); },
    release(sid: string): void { held.get(sid)?.(ocJson(200, HISTORY[sid] ?? [])); held.delete(sid); },
    asked: (path: string): boolean => gw.calls.some((c) => c.path === path),
  };
  const answer = (method: string, path: string, body: string): Answer => {
    if (path === "/pcbk/api/agents") {
      gw.agentCalls += 1;
      return o.agents ? o.agents() : ocJson(200, { agents: BUILTIN, own: 0, limit: 20 });
    }
    if (path === "/pcbk/api/oc/event") {
      return new Response(new ReadableStream<Uint8Array>({
        start(c) { ctl = c; c.enqueue(enc.encode(`data: ${JSON.stringify({ type: "server.connected", properties: {} })}\n\n`)); },
        cancel() { ctl = null; },
      }), { status: 200, headers: { "content-type": "text/event-stream" } });
    }
    if (path === "/pcbk/api/oc/experimental/session")
      return o.list ? o.list() : ocJson(200, [session(SES_K, "Агент по узлу 3"), session(SES_P, "Значение T1")]);
    if (method === "POST" && path === "/pcbk/api/oc/session") {
      gw.created += 1;
      return ocJson(200, session(`ses_new${gw.created}abcdefghijklmnopqr`));
    }
    const m = /^\/pcbk\/api\/oc\/session\/([^/]+)(\/.*)?$/.exec(path);
    if (m === null) return undefined;
    const [, sid, rest] = m;
    if (rest === undefined && method === "GET") return ocJson(200, session(sid!));
    if (rest === "/message" && method === "GET") {
      const n = (loads.get(sid!) ?? 0) + 1;
      loads.set(sid!, n);
      if (o.hold?.(sid!, n)) return new Promise<Response>((resolve) => { held.set(sid!, resolve); });
      return ocJson(200, HISTORY[sid!] ?? []);
    }
    if (rest === "/prompt_async") {
      gw.prompts.push({ session: sid!, body: JSON.parse(body) });
      return o.prompt?.(sid!) ?? new Response(null, { status: 204 });
    }
    return undefined;
  };
  vi.stubGlobal("fetch", vi.fn(async (input: Request | string, init?: RequestInit) => {
    const r = typeof input === "string" ? new Request(new URL(input, location.origin), init) : input;
    const path = new URL(r.url).pathname;
    const body = r.body ? await r.clone().text() : "";
    gw.calls.push({ method: r.method, path, body });
    return answer(r.method, path, body) ?? ocJson(404, { name: "NotFound", data: { message: "нет" } });
  }));
  return gw;
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const box = () => screen.getByRole("textbox", { name: "Ваш вопрос агенту" }) as HTMLTextAreaElement;
const agentLine = (c: HTMLElement) => c.querySelector(".agent-line")?.textContent ?? null;

// отправка как у студента: поле и кнопка доступны → текст → «Отправить» → prompt_async ушёл → ход кончился (session.idle)
async function send(gw: ReturnType<typeof chatGateway>, text: string) {
  const before = gw.prompts.length;
  await waitFor(() => expect(box().disabled).toBe(false));
  fireEvent.change(box(), { target: { value: text } });
  const send = await screen.findByRole("button", { name: "Отправить" }) as HTMLButtonElement;
  await waitFor(() => expect(send.disabled).toBe(false));
  fireEvent.click(send);
  await waitFor(() => expect(gw.prompts.length).toBe(before + 1));
  const sid = gw.prompts.at(-1)!.session;
  await waitFor(() => {
    gw.push({ type: "session.idle", properties: { sessionID: sid } });
    expect(screen.queryByRole("button", { name: "Отправить" })).toBeTruthy();
  });
  return gw.prompts.at(-1)!;
}

const pickAgent = async (name: RegExp) => fireEvent.click(await screen.findByRole("button", { name }));
const openThread = (c: HTMLElement, sid: string) =>
  fireEvent.click(c.querySelector(`[data-session-id="${sid}"] .thread-open`)!);

test("send_agent_follows_thread", async () => {
  const gw = chatGateway({ hold: (sid, n) => sid === SES_K && n === 1 });
  const { container } = render(<Chat onHalt={vi.fn()} loadAgents={createApi().agents} />);
  await waitFor(() => expect(container.querySelector(`[data-session-id="${SES_K}"]`)).toBeTruthy());
  expect(agentLine(container)).toBe("Агент: Помощник по данным");                 // новый разговор — помощник места

  await pickAgent(/Конструктор агентов/);
  expect(agentLine(container)).toBe("Агент: Конструктор агентов");
  const first = await send(gw, "привет");
  expect(first.body).toMatchObject({ agent: "konstruktor" });

  fireEvent.click(screen.getByRole("button", { name: "Новый разговор" }));
  const again = await send(gw, "ещё");
  expect(again.body).toMatchObject({ agent: "konstruktor" });                    // «Новый разговор» сохраняет выбор
  expect(again.session).not.toBe(first.session);

  await pickAgent(/Помощник по данным/);
  const plain = await send(gw, "что сейчас");
  expect(plain.body).not.toHaveProperty("agent");                                // помощник — default_agent места
  expect(plain.session).not.toBe(again.session);

  // прежний разговор Конструктора: пока история в пути, агент неизвестен — отправка закрыта (П14)
  openThread(container, SES_K);
  await waitFor(() => expect(gw.asked(`/pcbk/api/oc/session/${SES_K}/message`)).toBe(true));
  await waitFor(() => expect(box().disabled).toBe(true));
  expect(agentLine(container)).toBeNull();
  expect(container.querySelector('[aria-current="true"][data-agent]')).toBeNull();
  fireEvent.change(box(), { target: { value: "рано" } });                          // текст попал в поле — всё равно нет
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  fireEvent.keyDown(box(), { key: "Enter" });
  await sleep(100);
  expect(gw.prompts.filter((p) => p.session === SES_K)).toEqual([]);

  gw.release(SES_K);
  await screen.findByText("Какой узел и какие теги?");
  const next = await send(gw, "дальше");
  expect(next).toMatchObject({ session: SES_K, body: { agent: "konstruktor" } }); // агент — из истории разговора
  expect(screen.getByText("Агент: Конструктор агентов")).toBeTruthy();
  expect(container.querySelector('[aria-current="true"][data-agent]')?.getAttribute("data-agent")).toBe("konstruktor");
});

test("send_agent_after_reload_waits_for_history", async () => {
  // Review Focus 1: перезагрузка или «Разбудить» — чат открывает прежний разговор Конструктора, история ещё грузится;
  // выбор по умолчанию (помощник) не уходит в разговор Конструктора
  const gw = chatGateway({ hold: (sid, n) => sid === SES_K && n === 1 });
  const onThreadIdChange = vi.fn();
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents}
                                     onThreadIdChange={onThreadIdChange} />);
  await waitFor(() => expect(gw.asked(`/pcbk/api/oc/session/${SES_K}/message`)).toBe(true));
  expect(onThreadIdChange).toHaveBeenCalledWith(SES_K);
  await waitFor(() => expect(box().disabled).toBe(true));
  expect((screen.getByRole("button", { name: "Отправить" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(box(), { target: { value: "продолжим" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  await sleep(100);
  expect(gw.prompts).toEqual([]);
  gw.release(SES_K);
  const next = await send(gw, "узел 3, теги T1 и T2");
  expect(next).toMatchObject({ session: SES_K, body: { agent: "konstruktor" } });
  expect(agentLine(container)).toBe("Агент: Конструктор агентов");
});

test("send_agent_from_history_of_pomoshnik_thread", async () => {
  // выбран Конструктор, но открыт разговор помощника — ход уходит помощнику (без agent), а не выбранному
  const gw = chatGateway({});
  const { container } = render(<Chat onHalt={vi.fn()} loadAgents={createApi().agents} />);
  await pickAgent(/Конструктор агентов/);
  await waitFor(() => expect(container.querySelector(`[data-session-id="${SES_P}"]`)).toBeTruthy());
  openThread(container, SES_P);
  await screen.findByText("T1 = 12,5");
  const next = await send(gw, "а за смену?");
  expect(next.session).toBe(SES_P);
  expect(next.body).not.toHaveProperty("agent");
  expect(agentLine(container)).toBe("Агент: Помощник по данным");
  fireEvent.click(screen.getByRole("button", { name: "Новый разговор" }));       // а новый — снова с выбранным
  const fresh = await send(gw, "собери агента");
  expect(fresh.body).toMatchObject({ agent: "konstruktor" });
});

test("pick_before_previous_thread_opens", async () => {
  // m15: студент выбрал агента, пока список разговоров грузится, — прежний разговор не открывается поверх выбора
  let releaseList: (r: Response) => void = () => {};
  const gw = chatGateway({ list: () => new Promise<Response>((resolve) => { releaseList = resolve; }) });
  const onThreadIdChange = vi.fn();
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_P} loadAgents={createApi().agents}
                                     onThreadIdChange={onThreadIdChange} />);
  await pickAgent(/Конструктор агентов/);
  releaseList(ocJson(200, [session(SES_P, "Значение T1")]));                     // прежний разговор в списке есть
  await waitFor(() => expect(container.querySelector(`[data-session-id="${SES_P}"]`)).toBeTruthy());
  await sleep(150);
  expect(gw.calls.filter((c) => c.path.startsWith(`/pcbk/api/oc/session/${SES_P}`))).toEqual([]);
  expect(agentLine(container)).toBe("Агент: Конструктор агентов");
  const first = await send(gw, "привет");
  expect(first.session).not.toBe(SES_P);
  expect(first.body).toMatchObject({ agent: "konstruktor" });
  expect(onThreadIdChange.mock.calls.flat()).not.toContain(SES_P);
});

test("agent_line_shows_name_when_not_listed", async () => {
  // m14: агента разговора нет в списке (архив) — в строке имя; ход уходит ему (из истории), шлюз решит сам (R65)
  HISTORY["ses_archived0123456789abcdefg"] = [userMsg("ses_archived0123456789abcdefg", "msg_a1", "staryy-agent", "привет")];
  const gw = chatGateway({ list: () => ocJson(200, [session("ses_archived0123456789abcdefg", "Старый агент")]) });
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId="ses_archived0123456789abcdefg"
                                     loadAgents={createApi().agents} />);
  await screen.findByText("привет");
  await waitFor(() => expect(agentLine(container)).toBe("Агент: staryy-agent"));
  expect(container.querySelector('[aria-current="true"][data-agent]')).toBeNull();
  const next = await send(gw, "дальше");
  expect(next.body).toMatchObject({ agent: "staryy-agent" });
  delete HISTORY["ses_archived0123456789abcdefg"];
});

test("chat_agents_404_keeps_chat", async () => {
  // Review Focus 4: старый core без ручки — строка в панели, чат работает с помощником; строки потока нет
  const gw = chatGateway({ agents: () => ocJson(404, { name: "NotFound", data: { message: "нет" } }) });
  const { container } = render(<Chat onHalt={vi.fn()} loadAgents={createApi().agents} />);
  expect(await screen.findByText(T_AGENTS_DOWN)).toBeTruthy();
  expect(container.querySelectorAll("[data-agent]")).toHaveLength(0);
  const first = await send(gw, "что сейчас");
  expect(first.body).not.toHaveProperty("agent");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(gw.agentCalls).toBeLessThanOrEqual(2);                                   // монтирование и +1 с — без опроса
});

test("chat_without_loader_has_no_panel", async () => {
  // П11: без loadAgents (тесты Д6) — ни панели, ни строки агента, ни вызова ручки
  const gw = chatGateway({});
  const { container } = render(<Chat onHalt={vi.fn()} />);
  await waitFor(() => expect(container.querySelector(`[data-session-id="${SES_K}"]`)).toBeTruthy());
  expect(container.querySelector(".agents")).toBeNull();
  expect(container.querySelector(".agent-line")).toBeNull();
  const first = await send(gw, "вопрос");
  expect(first.body).not.toHaveProperty("agent");
  await sleep(1_200);
  expect(gw.agentCalls).toBe(0);
});

test("chat_agents_refresh_after_turn", async () => {
  // конец хода — через 1 с список снова: агент из pending становится ready, кнопка оживает
  let answers = 0;
  const gw = chatGateway({ agents: () => {
    answers += 1;
    return ocJson(200, { agents: [...BUILTIN, { ...MINE, state: answers === 1 ? "pending" : "ready" }], own: 1, limit: 20 });
  } });
  const { container, unmount } = render(<Chat onHalt={vi.fn()} loadAgents={createApi().agents} />);
  await waitFor(() => expect(button(/Мой агент/).disabled).toBe(true));
  expect(gw.agentCalls).toBe(1);
  await send(gw, "сохрани агента");
  await sleep(500);
  expect(gw.agentCalls).toBe(1);                                                  // не раньше 1 с
  await waitFor(() => expect(gw.agentCalls).toBe(2), { timeout: 2_000 });
  await waitFor(() => expect(button(/Мой агент/).disabled).toBe(false));
  expect(container.querySelector('[data-agent="moy-agent"]')!.getAttribute("data-state")).toBe("ready");
  fireEvent.click(button(/Мой агент/));                                            // клик по готовому — новый разговор
  expect(agentLine(container)).toBe("Агент: Мой агент");
  const mine = await send(gw, "что на узле 3");
  expect(mine.body).toMatchObject({ agent: "moy-agent" });
  unmount();
});

test("chat_agents_poll_stops_on_unmount", async () => {
  const gw = chatGateway({ agents: () => ocJson(200, { agents: [...BUILTIN, MINE], own: 1, limit: 20 }) });
  const { unmount } = render(<Chat onHalt={vi.fn()} loadAgents={createApi().agents} />);
  await waitFor(() => expect(gw.agentCalls).toBe(1));
  await send(gw, "сохрани агента");                                               // ход кончился — опрос взведён
  unmount();
  await sleep(1_300);
  expect(gw.agentCalls).toBe(1);
});

test("chat_agents_poll_stops_on_halt", async () => {
  // остановка обёртки («место спит» на prompt_async): ход сорвался (isRunning true → false), но опроса нет
  vi.spyOn(console, "error").mockImplementation(() => {});
  const gw = chatGateway({ agents: () => ocJson(200, { agents: [...BUILTIN, MINE], own: 1, limit: 20 }),
                           prompt: () => ocJson(503, { name: "WorkplaceNotReady", data: { message: "Рабочее место спит" } }) });
  const onHalt = vi.fn();
  render(<Chat onHalt={onHalt} loadAgents={createApi().agents} />);
  await waitFor(() => expect(gw.agentCalls).toBe(1));
  fireEvent.change(box(), { target: { value: "вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  await waitFor(() => expect(onHalt).toHaveBeenCalledWith("asleep"));
  await sleep(1_300);
  expect(gw.agentCalls).toBe(1);
});

test("background_history_reload_keeps_composer", async () => {
  // фоновое перечитывание истории (loadState снова loading) не закрывает поле: агент разговора уже известен
  const gw = chatGateway({ hold: (sid, n) => sid === SES_K && n === 2 });
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents} />);
  await screen.findByText("Какой узел и какие теги?");
  await waitFor(() => expect(agentLine(container)).toBe("Агент: Конструктор агентов"));
  const loads = () => gw.calls.filter((c) => c.path === `/pcbk/api/oc/session/${SES_K}/message`).length;
  expect(loads()).toBe(1);
  await act(async () => {                                                         // событие о незнакомом сообщении
    gw.push({ type: "message.part.updated", properties: { part: { id: "prt_x", sessionID: SES_K, messageID: "msg_x",
                                                                    type: "text", text: "…" } } });
  });
  await waitFor(() => expect(loads()).toBe(2));                                   // перечитывание в пути (придержано)
  await sleep(100);
  expect(box().disabled).toBe(false);
  expect(agentLine(container)).toBe("Агент: Конструктор агентов");
  gw.release(SES_K);
  const next = await send(gw, "дальше");
  expect(next.body).toMatchObject({ agent: "konstruktor" });
});
