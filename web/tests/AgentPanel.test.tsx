// Панель «Агенты» и агент разговора (Д7-R65, Review Focus 1 и 3 Д7б; preflight П9–П12, П14, m13–m16).
// Панель — одна, без рантайма. Chat — настоящий адаптер OpenCode, как в ChatView.test.tsx: сеть — подменённый
// глобальный fetch (шлюз OpenCode и GET /pcbk/api/agents через createApi), поток /event дописывает тест.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createOpenCodeThreadState } from "@assistant-ui/react-opencode";
import type { AgentItem } from "../src/agents";
import { createApi } from "../src/api";
import { AgentPanel } from "../src/components/AgentPanel";
import { Chat } from "../src/components/Chat";
import { seenAgent } from "../src/components/ThreadAgentWatch";
import {
  BUILTIN, CHAT_TIMEOUT_MS, HISTORY, SES_K, SES_P, agentLine, box, chatGateway, ocJson, openThread, pickAgent, send, session,
  sleep, userMsg,
} from "./chatGateway";
import {
  T_AGENT_BROKEN, T_AGENT_LINE, T_AGENT_PENDING, T_AGENTS, T_AGENTS_DOWN, T_AGENTS_EMPTY, T_MY_AGENTS, T_UNKNOWN,
} from "../src/texts";

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
    .toEqual(["Помощник технолога КДМ-1", "Конструктор агентов", "Мой агент", "Сломанный"]);
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

// --- агент открытого разговора: три состояния (П14) ---

test("seen_agent_states", () => {
  const B = "ses_bbbbbbbbbbbbbbbbbbbbbbbbbb";
  const loaded = (sid: string, agent?: string) => {
    const st = createOpenCodeThreadState(sid);
    const messages = agent === undefined ? {} : { m1: { id: "m1", info: { role: "user", agent }, parts: [], shadowParts: undefined } };
    return { ...st, loadState: { type: "ready" as const }, sync: { lastHistoryLoadAt: 1 },
             messageOrder: Object.keys(messages), messagesById: messages as never };
  };
  expect(seenAgent(undefined, createOpenCodeThreadState("__pending__"))).toEqual({ state: "none" });   // новый разговор
  expect(seenAgent(B, loaded(B, "konstruktor"))).toEqual({ state: "agent", name: "konstruktor" });
  expect(seenAgent(B, loaded(B))).toEqual({ state: "none" });                    // история есть, сообщений студента нет
  // рантайм разговора B ещё не привязан: состояние адаптера — прежнего разговора (или EMPTY) — не про B
  expect(seenAgent(B, loaded("ses_aaaaaaaaaaaaaaaaaaaaaaaaaa", "konstruktor"))).toEqual({ state: "loading" });
  expect(seenAgent(B, loaded("ses_aaaaaaaaaaaaaaaaaaaaaaaaaa"))).toEqual({ state: "loading" });
  expect(seenAgent(B, createOpenCodeThreadState("__pending__"))).toEqual({ state: "loading" });
  // история ни разу не загружена: idle, loading, error — неизвестно
  for (const loadState of [{ type: "idle" }, { type: "loading" }, { type: "error", error: new Error("x") }] as const)
    expect(seenAgent(B, { ...createOpenCodeThreadState(B), loadState })).toEqual({ state: "loading" });
  // фоновое перечитывание: loadState снова loading, а история уже была
  expect(seenAgent(B, { ...loaded(B, "konstruktor"), loadState: { type: "loading" } })).toEqual({ state: "agent", name: "konstruktor" });
  expect(seenAgent(B, { ...loaded(B), loadState: { type: "loading" } })).toEqual({ state: "none" });
  // сообщение студента пришло событием раньше первой загрузки — агент уже известен
  expect(seenAgent(B, { ...loaded(B, "konstruktor"), loadState: { type: "idle" }, sync: {} })).toEqual({ state: "agent", name: "konstruktor" });
});

// --- Chat: агент разговора (адаптер настоящий, сеть — подменённый fetch; двойник шлюза — ./chatGateway.ts) ---

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test("send_agent_follows_thread", async () => {
  const gw = chatGateway({ hold: (sid, n) => sid === SES_K && n === 1 });
  const { container } = render(<Chat onHalt={vi.fn()} loadAgents={createApi().agents} />);
  await waitFor(() => expect(container.querySelector(`[data-session-id="${SES_K}"]`)).toBeTruthy());
  expect(agentLine(container)).toBe("Агент: Помощник технолога КДМ-1");                 // новый разговор — помощник места

  await pickAgent(/Конструктор агентов/);
  expect(agentLine(container)).toBe("Агент: Конструктор агентов");
  const first = await send(gw, "привет");
  expect(first.body).toMatchObject({ agent: "konstruktor" });

  fireEvent.click(screen.getByRole("button", { name: "Новый разговор" }));
  const again = await send(gw, "ещё");
  expect(again.body).toMatchObject({ agent: "konstruktor" });                    // «Новый разговор» сохраняет выбор
  expect(again.session).not.toBe(first.session);

  await pickAgent(/Помощник технолога КДМ-1/);
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
}, CHAT_TIMEOUT_MS);

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
}, CHAT_TIMEOUT_MS);

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
  expect(agentLine(container)).toBe("Агент: Помощник технолога КДМ-1");
  fireEvent.click(screen.getByRole("button", { name: "Новый разговор" }));       // а новый — снова с выбранным
  const fresh = await send(gw, "собери агента");
  expect(fresh.body).toMatchObject({ agent: "konstruktor" });
}, CHAT_TIMEOUT_MS);

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
  expect(onThreadIdChange).not.toHaveBeenCalled();                               // и не забыт: решать нечего (как Д6б)
  expect(agentLine(container)).toBe("Агент: Конструктор агентов");
  const first = await send(gw, "привет");
  expect(first.session).not.toBe(SES_P);
  expect(first.body).toMatchObject({ agent: "konstruktor" });
  expect(onThreadIdChange.mock.calls.flat()).not.toContain(SES_P);
}, CHAT_TIMEOUT_MS);

test("pick_unlocks_before_list_loaded", async () => {
  // m1 и m15: выбор агента — решение студента (новый разговор с ним): отправка открыта, не дожидаясь списка
  // разговоров, а пришедший список прежний разговор поверх не открывает
  let releaseList: (r: Response) => void = () => {};
  const gw = chatGateway({ list: () => new Promise<Response>((resolve) => { releaseList = resolve; }) });
  const onThreadIdChange = vi.fn();
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents}
                                     onThreadIdChange={onThreadIdChange} />);
  await screen.findByRole("button", { name: /Помощник технолога КДМ-1/ });
  expect(box().disabled).toBe(true);                                             // решения ещё нет (m1)
  await pickAgent(/Помощник технолога КДМ-1/);
  const first = await send(gw, "что сейчас");                                    // список всё ещё в пути
  expect(first.session).not.toBe(SES_K);
  expect(first.body).not.toHaveProperty("agent");
  releaseList(ocJson(200, [session(SES_K, "Агент по узлу 3")]));
  await waitFor(() => expect(container.querySelector(`[data-session-id="${SES_K}"]`)).toBeTruthy());
  await sleep(150);
  expect(gw.loads(SES_K)).toBe(0);
  expect(onThreadIdChange.mock.calls.flat()).not.toContain(SES_K);
}, CHAT_TIMEOUT_MS);

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
}, CHAT_TIMEOUT_MS);

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
}, CHAT_TIMEOUT_MS);

test("chat_without_loader_has_no_panel", async () => {
  // П11: без loadAgents (тесты Д6) — ни панели, ни строки агента, ни вызова ручки
  const gw = chatGateway({});
  const { container } = render(<Chat onHalt={vi.fn()} />);
  await waitFor(() => expect(container.querySelector(`[data-session-id="${SES_K}"]`)).toBeTruthy());
  expect(container.querySelector(".agents")).toBeNull();
  expect(container.querySelector(".agent-line")).toBeNull();
  const first = await send(gw, "вопрос");
  expect(first.body).not.toHaveProperty("agent");
  await sleep(1_200);                                                             // и после конца хода
  expect(gw.agentCalls).toBe(0);
}, CHAT_TIMEOUT_MS);

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
  await waitFor(() => expect(gw.agentCalls).toBe(2), { timeout: 5_000 });
  expect(gw.agentAt[1]! - gw.idleFrom).toBeGreaterThanOrEqual(990);              // не раньше 1 с после конца хода
  await waitFor(() => expect(button(/Мой агент/).disabled).toBe(false));
  expect(container.querySelector('[data-agent="moy-agent"]')!.getAttribute("data-state")).toBe("ready");
  fireEvent.click(button(/Мой агент/));                                            // клик по готовому — новый разговор
  expect(agentLine(container)).toBe("Агент: Мой агент");
  const mine = await send(gw, "что на узле 3");
  expect(mine.body).toMatchObject({ agent: "moy-agent" });
  unmount();
}, CHAT_TIMEOUT_MS);

test("chat_agents_poll_stops_on_unmount", async () => {
  const gw = chatGateway({ agents: () => ocJson(200, { agents: [...BUILTIN, MINE], own: 1, limit: 20 }) });
  const { unmount } = render(<Chat onHalt={vi.fn()} loadAgents={createApi().agents} />);
  await waitFor(() => expect(gw.agentCalls).toBe(1));
  await send(gw, "сохрани агента");                                               // ход кончился — опрос взведён
  const atUnmount = gw.agentCalls;
  unmount();
  await sleep(3_500);                                                             // живой опрос с pending — раз в ≤3 с
  expect(gw.agentCalls).toBe(atUnmount);
}, CHAT_TIMEOUT_MS);

test("chat_agents_poll_stops_on_halt", async () => {
  // ход кончился — опрос взведён (+1 с); следующий ход ловит «место спит» (503 на prompt_async): обёртка остановлена,
  // взведённый опрос снят, а сорвавшийся ход (isRunning true → false) нового не взводит
  vi.spyOn(console, "error").mockImplementation(() => {});
  let prompts = 0;
  const gw = chatGateway({
    agents: () => ocJson(200, { agents: [...BUILTIN, MINE], own: 1, limit: 20 }),
    prompt: () => (prompts += 1) === 1 ? undefined
      : ocJson(503, { name: "WorkplaceNotReady", data: { message: "Рабочее место спит" } }),
  });
  const onHalt = vi.fn();
  render(<Chat onHalt={onHalt} initialSessionId={SES_P} loadAgents={createApi().agents} />);
  await screen.findByText("T1 = 12,5");
  expect(gw.agentCalls).toBe(1);
  await send(gw, "что сейчас");                                                   // ход кончился — опрос через 1 с
  fireEvent.change(box(), { target: { value: "а за смену?" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  await waitFor(() => expect(onHalt).toHaveBeenCalledWith("asleep"));
  const atHalt = gw.agentCalls;                                                   // +1 с под нагрузкой мог успеть раньше
  expect(prompts).toBe(2);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Отправить" })).toBeTruthy());   // ход сорвался
  await sleep(3_500);                                                             // живой опрос с pending — раз в ≤3 с
  expect(gw.agentCalls).toBe(atHalt);
}, CHAT_TIMEOUT_MS);

test("agent_appears_after_leaving_konstruktor_mid_turn", async () => {
  // итоговая доводка Д7б, m-2: студент ушёл из разговора Конструктора, пока ход сохранения шёл, и служба записала
  // агента уже после ухода. Ход кончился в оставленном разговоре — список через 1 с, pending → опрос → ready
  let saved = false;
  let afterSave = 0;
  const gw = chatGateway({ agents: () => {
    if (!saved) return ocJson(200, { agents: BUILTIN, own: 0, limit: 20 });
    afterSave += 1;
    return ocJson(200, { agents: [...BUILTIN, { ...MINE, state: afterSave === 1 ? "pending" : "ready" }], own: 1, limit: 20 });
  } });
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents} />);
  await screen.findByText("Какой узел и какие теги?");
  await waitFor(() => expect(box().disabled).toBe(false));
  fireEvent.change(box(), { target: { value: "да, сохраняй" } });
  const sendButton = await screen.findByRole("button", { name: "Отправить" }) as HTMLButtonElement;
  await waitFor(() => expect(sendButton.disabled).toBe(false));
  fireEvent.click(sendButton);
  await waitFor(() => expect(gw.prompts).toHaveLength(1));
  gw.push({ type: "session.status", properties: { sessionID: SES_K, status: { type: "busy" } } });
  await screen.findByRole("button", { name: "Стоп" });                            // ход Конструктора идёт
  openThread(container, SES_P);                                                  // студент ушёл до конца хода
  await screen.findByText("T1 = 12,5");
  await sleep(1_500);                                                             // смена разговора — список перечитан,
  const beforeEnd = gw.agentCalls;                                                // агента ещё нет: опроса дальше нет
  expect(container.querySelector('[data-agent="moy-agent"]')).toBeNull();
  saved = true;                                                                   // служба записала агента в том ходе
  const endedAt = performance.now();
  gw.push({ type: "session.status", properties: { sessionID: SES_K, status: { type: "idle" } } },
          { type: "session.idle", properties: { sessionID: SES_K } });
  await waitFor(() => expect(gw.agentCalls).toBe(beforeEnd + 1), { timeout: 5_000 });
  expect(gw.agentAt.at(-1)! - endedAt).toBeGreaterThanOrEqual(990);              // через 1 с после конца хода
  await waitFor(() => expect(button(/Мой агент/).disabled).toBe(false), { timeout: 8_000 });   // pending → ready
  expect(agentLine(container)).toBe("Агент: Помощник технолога КДМ-1");                 // открытый разговор не тронут
}, CHAT_TIMEOUT_MS);

test("agents_refresh_on_thread_switch", async () => {
  // m-2: смена открытого разговора студентом — список через 1 с (переход из идущего хода — Б3-m4, теперь намеренно).
  // Открытие прежнего разговора при монтировании — не смена: якоря опроса до первого конца хода нет (m16)
  const gw = chatGateway({ agents: () => ocJson(200, { agents: [...BUILTIN, MINE], own: 1, limit: 20 }) });
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_P} loadAgents={createApi().agents} />);
  await screen.findByText("T1 = 12,5");
  await sleep(1_500);
  expect(gw.agentCalls).toBe(1);                                                  // только монтирование
  const switchedAt = performance.now();
  openThread(container, SES_K);
  await screen.findByText("Какой узел и какие теги?");
  await waitFor(() => expect(gw.agentCalls).toBe(2), { timeout: 5_000 });
  expect(gw.agentAt[1]! - switchedAt).toBeGreaterThanOrEqual(990);
  await sleep(3_500);                                                             // pending — опрос раз в 3 с от якоря
  expect(gw.agentCalls).toBeGreaterThanOrEqual(3);
}, CHAT_TIMEOUT_MS);

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
}, CHAT_TIMEOUT_MS);

// --- доводка р1 ---

test("history_first_load_error_reopen_reloads", async () => {
  // I1: первая загрузка истории упала — агент разговора неизвестен, отправка закрыта. Экземпляр разговора и контроллер
  // адаптер держит до снятия чата и load() сам не повторяет; каждое повторное открытие перечитывает историю (одна
  // загрузка на открытие, без таймеров). Ошибка снова видна строкой — не пустой разговор без текста
  vi.spyOn(console, "error").mockImplementation(() => {});                       // адаптер пишет сбой загрузки
  const gw = chatGateway({ fail: (sid, n) => sid === SES_K && n <= 2 });
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents} />);
  await waitFor(() => expect(gw.loads(SES_K)).toBe(1));
  expect((await screen.findByRole("alert")).textContent).toBe(T_UNKNOWN);    // 500 — общий текст Д6
  await sleep(300);
  expect(gw.loads(SES_K)).toBe(1);                                               // сам не повторяет — тупик без правки
  expect(box().disabled).toBe(true);
  expect(agentLine(container)).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Новый разговор" }));
  await waitFor(() => expect(box().disabled).toBe(false));                       // новый разговор — пиши
  openThread(container, SES_K);                                                  // снова открыт — вторая загрузка, 500
  await waitFor(() => expect(gw.loads(SES_K)).toBe(2));
  expect((await screen.findByRole("alert")).textContent).toBe(T_UNKNOWN);    // 500 — общий текст Д6
  await sleep(1_000);
  expect(gw.loads(SES_K)).toBe(2);                                               // без петли: одна загрузка на открытие
  expect(box().disabled).toBe(true);

  fireEvent.click(screen.getByRole("button", { name: "Новый разговор" }));
  await waitFor(() => expect(box().disabled).toBe(false));
  openThread(container, SES_K);                                                  // третья загрузка — 200
  await screen.findByText("Какой узел и какие теги?");
  expect(gw.loads(SES_K)).toBe(3);
  await waitFor(() => expect(agentLine(container)).toBe("Агент: Конструктор агентов"));
  const next = await send(gw, "дальше");
  expect(next).toMatchObject({ session: SES_K, body: { agent: "konstruktor" } });
  expect(gw.loads(SES_K)).toBe(3);
}, CHAT_TIMEOUT_MS);

test("history_reopen_after_success_does_not_reload", async () => {
  // перечитывание — только после сбоя первой загрузки: открытие загруженного разговора истории не перечитывает
  const gw = chatGateway({});
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents} />);
  await screen.findByText("Какой узел и какие теги?");
  fireEvent.click(screen.getByRole("button", { name: "Новый разговор" }));
  await waitFor(() => expect(box().disabled).toBe(false));
  openThread(container, SES_K);
  await waitFor(() => expect(agentLine(container)).toBe("Агент: Конструктор агентов"));
  await sleep(300);
  expect(gw.loads(SES_K)).toBe(1);
}, CHAT_TIMEOUT_MS);

test("send_before_list_loaded_after_reload", async () => {
  // m1: перезагрузка или «Разбудить» — пока не решено, открывать ли прежний разговор (список в пути), отправка закрыта:
  // иначе ход ушёл бы помощнику в новый разговор мимо прежнего разговора Конструктора
  let releaseList: (r: Response) => void = () => {};
  const gw = chatGateway({ list: () => new Promise<Response>((resolve) => { releaseList = resolve; }) });
  const onThreadIdChange = vi.fn();
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents}
                                     onThreadIdChange={onThreadIdChange} />);
  await waitFor(() => expect(gw.agentCalls).toBe(1));
  await screen.findByRole("button", { name: /Конструктор агентов/ });           // панель уже есть, список разговоров — нет
  expect(box().disabled).toBe(true);
  expect(agentLine(container)).toBeNull();
  fireEvent.change(box(), { target: { value: "продолжим" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  fireEvent.keyDown(box(), { key: "Enter" });
  await sleep(150);
  expect(gw.prompts).toEqual([]);
  expect(gw.created).toBe(0);
  // кадры от прихода списка до открытого прежнего разговора: поле не открывается, пока разговор ещё новый — блокировка
  // снимается после switchToThread, а не до него (иначе — кадр «поле открыто, новый разговор, помощник»)
  const frames: string[] = [];
  const frame = (): void => {
    const active = container.querySelector('.thread-item[data-active="true"]')?.getAttribute("data-session-id") ?? "new";
    frames.push(`${box().disabled ? "закрыто" : "открыто"}|${active}|${agentLine(container) ?? "-"}`);
  };
  const observer = new MutationObserver(frame);
  observer.observe(container, { attributes: true, subtree: true, childList: true, characterData: true });
  releaseList(ocJson(200, [session(SES_K, "Агент по узлу 3")]));
  await screen.findByText("Какой узел и какие теги?");                           // открыт прежний разговор
  await waitFor(() => expect(agentLine(container)).toBe("Агент: Конструктор агентов"));
  frame();
  observer.disconnect();
  expect(frames.filter((f) => f.startsWith("открыто|new"))).toEqual([]);
  expect(frames.filter((f) => f.startsWith("открыто"))).toEqual(
    frames.filter((f) => f === `открыто|${SES_K}|Агент: Конструктор агентов`));   // открыто — только в прежнем
  const next = await send(gw, "узел 3");
  expect(next).toMatchObject({ session: SES_K, body: { agent: "konstruktor" } });
  expect(onThreadIdChange.mock.calls).toEqual([[SES_K]]);
}, CHAT_TIMEOUT_MS);

test("send_after_list_failure_on_reload", async () => {
  // m1, «и нет loadError»: список разговоров упал — решать нечего, отправка открыта (новый разговор, помощник)
  vi.spyOn(console, "error").mockImplementation(() => {});                       // библиотека пишет сбой списка
  const gw = chatGateway({ list: () => ocJson(500, { name: "UnknownError", data: { message: "сбой" } }) });
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents} />);
  const first = await send(gw, "что сейчас");
  expect(first.session).not.toBe(SES_K);
  expect(first.body).not.toHaveProperty("agent");
  expect(agentLine(container)).toBe("Агент: Помощник технолога КДМ-1");
  expect(gw.loads(SES_K)).toBe(0);
}, CHAT_TIMEOUT_MS);

test("send_after_previous_not_listed_on_reload", async () => {
  // m1: прежнего разговора в списке нет (архив) — решение принято, отправка открыта, App забывает прежний
  const gw = chatGateway({ list: () => ocJson(200, [session(SES_P, "Значение T1")]) });
  const onThreadIdChange = vi.fn();
  render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents} onThreadIdChange={onThreadIdChange} />);
  const first = await send(gw, "что сейчас");
  expect(first.session).not.toBe(SES_K);
  expect(onThreadIdChange.mock.calls[0]).toEqual([undefined]);
}, CHAT_TIMEOUT_MS);

test("agent_line_builtin_titles_without_list", async () => {
  // m3: список агентов недоступен (404) — у встроенных агентов в строке название, а не системное имя
  const gw = chatGateway({ agents: () => ocJson(404, { name: "NotFound", data: { message: "нет" } }) });
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents} />);
  await screen.findByText(T_AGENTS_DOWN);
  await screen.findByText("Какой узел и какие теги?");
  await waitFor(() => expect(agentLine(container)).toBe("Агент: Конструктор агентов"));
  fireEvent.click(screen.getByRole("button", { name: "Новый разговор" }));
  await waitFor(() => expect(agentLine(container)).toBe("Агент: Помощник технолога КДМ-1"));
  void gw;
}, CHAT_TIMEOUT_MS);

// --- доводка р1б ---

test("history_failed_threads_direct_switch", async () => {
  // I1, ключ RetryHistory — сессия: переход между двумя разговорами с упавшей первой загрузкой напрямую, без «Новый
  // разговор», — каждое открытие перечитывает историю своего разговора
  vi.spyOn(console, "error").mockImplementation(() => {});                       // адаптер пишет сбой загрузки
  const gw = chatGateway({ fail: (sid, n) => n === 1 && (sid === SES_K || sid === SES_P) });
  const { container } = render(<Chat onHalt={vi.fn()} loadAgents={createApi().agents} />);
  await waitFor(() => expect(container.querySelector(`[data-session-id="${SES_K}"]`)).toBeTruthy());
  openThread(container, SES_K);
  await waitFor(() => expect(gw.loads(SES_K)).toBe(1));
  await screen.findByRole("alert");
  openThread(container, SES_P);                                                  // прямо в другой упавший
  await waitFor(() => expect(gw.loads(SES_P)).toBe(1));
  await sleep(300);
  expect(gw.loads(SES_K)).toBe(1);
  expect(box().disabled).toBe(true);
  openThread(container, SES_K);                                                  // снова K — вторая загрузка, 200
  await screen.findByText("Какой узел и какие теги?");
  expect(gw.loads(SES_K)).toBe(2);
  expect(gw.loads(SES_P)).toBe(1);
  await waitFor(() => expect(agentLine(container)).toBe("Агент: Конструктор агентов"));
  const next = await send(gw, "дальше");
  expect(next).toMatchObject({ session: SES_K, body: { agent: "konstruktor" } });
}, CHAT_TIMEOUT_MS);
