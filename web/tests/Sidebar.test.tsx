// Список разговоров слева (Д6-R52, Review Focus 4 Д6б), сбой интерфейса (ErrorBoundary → ui_crash) и отметка
// старта для загрузчика (window.__pcbkStarted, preflight Ф9).
// Список — useRemoteThreadListRuntime с адаптером из памяти: видно, что ушло в адаптер (= в шлюз у OpenCode).
import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import {
  AssistantRuntimeProvider, useExternalStoreRuntime, useRemoteThreadListRuntime, type AssistantRuntime,
  type RemoteThreadListAdapter, type ThreadMessageLike,
} from "@assistant-ui/react";
import { StrictMode, useEffect, useLayoutEffect, useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { App, type AppProps } from "../src/App";
import type { Api, Reply } from "../src/api";
import type { ChatProps } from "../src/components/Chat";
import { ErrorBoundary } from "../src/components/ErrorBoundary";
import { ThreadSidebar, useTitleFollowsSession } from "../src/components/ThreadSidebar";
import { mountChat } from "../src/mount";
import type { startStatusPolling } from "../src/status";
import { MAX_TITLE, T_UI_CRASH } from "../src/texts";

afterEach(() => { vi.restoreAllMocks(); });

// --- список: адаптер из памяти ---

type Row = { remoteId: string; status: "regular" | "archived"; title: string };

function memoryAdapter(rows: Row[]) {
  const meta = (r: Row) => ({ ...r, externalId: r.remoteId });
  const adapter = {
    list: vi.fn(async () => ({ threads: rows.map(meta) })),
    rename: vi.fn(async (_id: string, _title: string) => {}),
    archive: vi.fn(async (_id: string) => {}),
    unarchive: vi.fn(async (_id: string) => {}),
    delete: vi.fn(async (_id: string) => {}),
    initialize: vi.fn(async (_threadId: string) => ({ remoteId: "ses_new", externalId: "ses_new" })),
    generateTitle: vi.fn(async () => new ReadableStream({ start(c) { c.close(); } }) as never),
    fetch: vi.fn(async (id: string) => meta(rows.find((r) => r.remoteId === id)!)),
  } satisfies RemoteThreadListAdapter;
  return adapter;
}

const NO_MESSAGES: ThreadMessageLike[] = [];
const onNew = async () => {};
const convert = (m: ThreadMessageLike) => m;
const useThreadRuntime = () =>
  useExternalStoreRuntime<ThreadMessageLike>({ messages: NO_MESSAGES, onNew, convertMessage: convert });

function SidebarHarness(p: { adapter: RemoteThreadListAdapter; initialThreadId?: string; onError?(e: unknown): void }) {
  const runtime = useRemoteThreadListRuntime({ adapter: p.adapter, initialThreadId: p.initialThreadId, runtimeHook: useThreadRuntime });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadSidebar runtime={runtime} onError={p.onError} />
    </AssistantRuntimeProvider>
  );
}

const ROWS: Row[] = [
  { remoteId: "ses_a", status: "regular", title: "Первый разговор" },
  { remoteId: "ses_b", status: "archived", title: "Старый разговор" },
  { remoteId: "ses_c", status: "regular", title: "Третий разговор" },
];
const rowOf = (sid: string) => document.querySelector<HTMLElement>(`[data-session-id="${sid}"]`);

test("sidebar_shows_active_only_and_no_unarchive_or_delete", async () => {
  const adapter = memoryAdapter(ROWS);
  render(<SidebarHarness adapter={adapter} />);
  await screen.findByText("Первый разговор");
  expect(screen.getByText("Третий разговор")).toBeTruthy();
  expect(screen.queryByText("Старый разговор")).toBeNull();                       // архивный не виден
  expect(rowOf("ses_b")).toBeNull();
  for (const sid of ["ses_a", "ses_c"]) {                                         // у строк — id разговора OpenCode
    const row = rowOf(sid)!;
    expect(within(row).getAllByRole("button").map((b) => b.textContent)).toEqual(
      [sid === "ses_a" ? "Первый разговор" : "Третий разговор", "Переименовать", "В архив"]);
  }
  for (const name of [/Из архива/, /Разархив/, /Удалить/, /Восстановить/])
    expect(screen.queryByRole("button", { name })).toBeNull();
  expect(screen.getByRole("button", { name: "Новый разговор" })).toBeTruthy();
  fireEvent.click(within(rowOf("ses_c")!).getByRole("button", { name: "В архив" }));
  await waitFor(() => expect(rowOf("ses_c")).toBeNull());                         // ушёл из списка
  expect(adapter.archive).toHaveBeenCalledWith("ses_c");
  expect(adapter.unarchive).not.toHaveBeenCalled(); expect(adapter.delete).not.toHaveBeenCalled();
});

test("sidebar_archive_open_thread_switches_to_new", async () => {
  // Review Focus 4: архив открытого разговора — чат уходит на новый, «из архива» не зовётся
  const adapter = memoryAdapter(ROWS);
  render(<SidebarHarness adapter={adapter} initialThreadId="ses_a" />);
  await waitFor(() => expect(rowOf("ses_a")?.getAttribute("aria-current")).toBe("true"));
  fireEvent.click(within(rowOf("ses_a")!).getByRole("button", { name: "В архив" }));
  await waitFor(() => expect(rowOf("ses_a")).toBeNull());
  expect(adapter.archive).toHaveBeenCalledWith("ses_a");
  expect(screen.getByRole("button", { name: "Новый разговор" }).getAttribute("aria-current")).toBe("true");
  expect(adapter.unarchive).not.toHaveBeenCalled(); expect(adapter.delete).not.toHaveBeenCalled();
});

test("sidebar_rename_limits", async () => {
  const adapter = memoryAdapter(ROWS);
  render(<SidebarHarness adapter={adapter} />);
  await screen.findByText("Первый разговор");
  const row = rowOf("ses_a")!;
  fireEvent.click(within(row).getByRole("button", { name: "Переименовать" }));
  const field = within(row).getByRole("textbox", { name: "Новое имя разговора" }) as HTMLInputElement;
  expect(field.value).toBe("Первый разговор");                                     // поле — с нынешним именем
  expect(document.activeElement).toBe(field);                                     // в фокусе и выделено: ввод заменит имя
  expect([field.selectionStart, field.selectionEnd]).toEqual([0, "Первый разговор".length]);
  expect(field.name).toBe("title");
  expect(field.maxLength).toBe(MAX_TITLE);
  for (const bad of ["", "   ", "я".repeat(MAX_TITLE + 1)]) {
    fireEvent.change(field, { target: { value: bad } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(within(row).getByText(`Имя разговора — от 1 до ${MAX_TITLE} знаков`)).toBeTruthy();   // подсказка
    expect(within(row).getByRole("textbox")).toBe(field);                         // поле осталось
  }
  expect(adapter.rename).not.toHaveBeenCalled();
  fireEvent.keyDown(field, { key: "Escape" });                                    // отмена — без вызова
  expect(within(row).queryByRole("textbox")).toBeNull();
  expect(within(row).queryByText(/Имя разговора — от 1/)).toBeNull();
  expect(adapter.rename).not.toHaveBeenCalled();
  const long = "я".repeat(MAX_TITLE);
  fireEvent.click(within(row).getByRole("button", { name: "Переименовать" }));
  fireEvent.change(within(row).getByRole("textbox"), { target: { value: long } });
  fireEvent.keyDown(within(row).getByRole("textbox"), { key: "Enter" });
  await waitFor(() => expect(adapter.rename).toHaveBeenCalledWith("ses_a", long));
  expect(adapter.rename).toHaveBeenCalledTimes(1);
  expect(await within(row).findByText(long)).toBeTruthy();                        // имя в строке — сразу
  fireEvent.click(within(rowOf("ses_c")!).getByRole("button", { name: "Переименовать" }));
  fireEvent.change(within(rowOf("ses_c")!).getByRole("textbox"), { target: { value: "  Разбор  " } });
  fireEvent.click(within(rowOf("ses_c")!).getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(adapter.rename).toHaveBeenCalledWith("ses_c", "Разбор"));   // пробелы по краям срезаны
});

test("sidebar_rename_failure_goes_to_error_line", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const adapter = memoryAdapter(ROWS);
  const refused = new Error("refused");
  adapter.rename.mockRejectedValueOnce(refused);
  const onError = vi.fn();
  render(<SidebarHarness adapter={adapter} onError={onError} />);
  await screen.findByText("Первый разговор");
  const row = rowOf("ses_a")!;
  fireEvent.click(within(row).getByRole("button", { name: "Переименовать" }));
  fireEvent.change(within(row).getByRole("textbox"), { target: { value: "Новое имя" } });
  fireEvent.keyDown(within(row).getByRole("textbox"), { key: "Enter" });
  await waitFor(() => expect(onError).toHaveBeenCalledWith(refused));             // текст отказа — строкой ошибки чата
  await waitFor(() => expect(within(row).getByText("Первый разговор")).toBeTruthy());   // имя вернулось
});

// --- App: сбой интерфейса и отметка старта ---

type Answer = [number, unknown];
function fakeApi(o: { me?: Answer; workplace?: Answer }) {
  const reply = (a: Answer | undefined) => async (): Promise<Reply> => ({ status: a?.[0] ?? 500, body: a?.[1] ?? null });
  return {
    me: vi.fn(reply(o.me)), workplace: vi.fn(reply(o.workplace)), wake: vi.fn(reply(undefined)),
    logout: vi.fn(reply([200, { ok: true }])), ackNotice: vi.fn(reply([204, null])), agents: vi.fn(reply(undefined)),
    report: vi.fn(async (_kind: string) => {}),
  } satisfies Api;
}
const okPolling: typeof startStatusPolling = (o) => { o.onView({ level: "ok", text: "Стенд работает" }); return () => {}; };
const STUDENT: Answer = [200, { login: "s", role: "student", place: "03", notice: null }];
const READY: Answer = [200, { state: "ready" }];

test("error_boundary_reports_crash_and_restarts", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});                         // React пишет пойманный сбой в консоль
  let broken = true;
  let mounts = 0;
  const Crashy = (_p: ChatProps) => {
    if (broken) throw new Error("сбой отрисовки");
    useEffect(() => { mounts += 1; }, []);
    return <div data-testid="fake-chat" />;
  };
  const a = fakeApi({ me: STUDENT, workplace: READY });
  render(<App api={a} navigate={vi.fn()} ChatImpl={Crashy} startPolling={okPolling} />);
  expect(await screen.findByText(T_UI_CRASH)).toBeTruthy();
  expect(screen.getByText("Вы вошли как s")).toBeTruthy();                        // шапка жива: сбой пойман вокруг чата
  expect(a.report.mock.calls).toEqual([["ui_crash"]]);                             // отчёт — один
  broken = false;
  fireEvent.click(screen.getByRole("button", { name: "Перезапустить чат" }));
  await screen.findByTestId("fake-chat");                                         // перемонтирование
  await waitFor(() => expect(mounts).toBe(1));
  expect(screen.queryByText(T_UI_CRASH)).toBeNull();
  expect(a.report).toHaveBeenCalledTimes(1);
  expect(a.wake).not.toHaveBeenCalled();
});

test("error_boundary_reports_each_crash_once", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const onCrash = vi.fn();
  let broken = true;
  // сбой в фазе фиксации: React передаёт границе ошибку каждого потомка — отчёт всё равно один
  const Crashy = () => {
    useLayoutEffect(() => { if (broken) throw new Error("сбой"); }, []);
    return <p>чат</p>;
  };
  render(<ErrorBoundary onCrash={onCrash}><Crashy /><Crashy /></ErrorBoundary>);   // два потомка падают разом
  expect(onCrash).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Перезапустить чат" }));     // всё ещё сломан — новый сбой, новый отчёт
  expect(onCrash).toHaveBeenCalledTimes(2);
  broken = false;
  fireEvent.click(screen.getByRole("button", { name: "Перезапустить чат" }));
  expect(screen.getAllByText("чат")).toHaveLength(2);
  expect(onCrash).toHaveBeenCalledTimes(2);
});

test("app_marks_started_once", async () => {
  const started = vi.fn();
  window.__pcbkStarted = started;
  try {
    const a = fakeApi({ me: STUDENT, workplace: READY });
    let answer: (r: Reply) => void = () => {};
    a.me.mockImplementation(() => new Promise<Reply>((resolve) => { answer = resolve; }));
    const Chat = (_p: ChatProps) => <div data-testid="fake-chat" />;
    render(<StrictMode><App api={a} navigate={vi.fn()} ChatImpl={Chat} startPolling={okPolling} /></StrictMode>);   // эффекты — дважды
    await waitFor(() => expect(started).toHaveBeenCalledTimes(1));                // /me ещё в пути — старт уже отмечен (Ф9)
    answer({ status: STUDENT[0], body: STUDENT[1] });
    await screen.findByTestId("fake-chat");
    expect(started).toHaveBeenCalledTimes(1);                                       // ровно один раз
  } finally {
    delete window.__pcbkStarted;
  }
  const b = fakeApi({ me: STUDENT, workplace: READY });                             // без загрузчика (edge до Б4) App работает
  render(<App api={b} navigate={vi.fn()} ChatImpl={(_p: ChatProps) => <div data-testid="no-boot-chat" />} startPolling={okPolling} />);
  expect(await screen.findByTestId("no-boot-chat")).toBeTruthy();
});

test("app_passes_reports_of_chat", async () => {
  const a = fakeApi({ me: STUDENT, workplace: READY });
  const Chat = (p: ChatProps) => (
    <div data-testid="fake-chat">
      <button type="button" onClick={() => p.onReport?.("turn_silent")}>silent</button>
      <button type="button" onClick={() => p.onReport?.("stream_down")}>down</button>
    </div>
  );
  render(<App api={a} navigate={vi.fn()} ChatImpl={Chat} startPolling={okPolling} />);
  await screen.findByTestId("fake-chat");
  fireEvent.click(screen.getByRole("button", { name: "silent" }));
  fireEvent.click(screen.getByRole("button", { name: "down" }));
  expect(a.report.mock.calls).toEqual([["turn_silent"], ["stream_down"]]);
});

// --- доводка 1, I1: «Перезапустить чат» — новое монтирование с последним открытым разговором ---

// чат, которым управляет тест: пишет initialSessionId каждого монтирования, по кнопкам — разговор и сбой
function crashableChat() {
  const mounts: (string | undefined)[] = [];
  const Chat = (p: ChatProps) => {
    const [broken, setBroken] = useState(false);
    useEffect(() => { mounts.push(p.initialSessionId); }, []);
    if (broken) throw new Error("сбой отрисовки");
    return (
      <div data-testid="fake-chat">
        <button type="button" onClick={() => p.onThreadIdChange?.("ses_B")}>B</button>
        <button type="button" onClick={() => p.onThreadIdChange?.(undefined)}>archived</button>
        <button type="button" onClick={() => setBroken(true)}>crash</button>
      </div>
    );
  };
  return { Chat, mounts };
}

test("restart_opens_last_session", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const { Chat, mounts } = crashableChat();
  const a = fakeApi({ me: STUDENT, workplace: READY });
  render(<App api={a} navigate={vi.fn()} ChatImpl={Chat} startPolling={okPolling} />);
  await screen.findByTestId("fake-chat");
  fireEvent.click(screen.getByRole("button", { name: "B" }));                     // работа в B
  fireEvent.click(screen.getByRole("button", { name: "crash" }));
  fireEvent.click(await screen.findByRole("button", { name: "Перезапустить чат" }));
  await screen.findByTestId("fake-chat");
  await waitFor(() => expect(mounts).toEqual([undefined, "ses_B"]));              // B, а не разговор прошлого монтирования
  expect(a.report.mock.calls).toEqual([["ui_crash"]]);
});

test("second_crash_after_restart_opens_new", async () => {
  // итоговая доводка Д6б, Б3-m6: чат, смонтированный «Перезапустить чат», упал снова — разговор мог ронять чат своим
  // содержимым: следующий перезапуск — с новым разговором, а не петля ui_crash на прежнем
  vi.spyOn(console, "error").mockImplementation(() => {});
  const { Chat, mounts } = crashableChat();
  const a = fakeApi({ me: STUDENT, workplace: READY });
  render(<App api={a} navigate={vi.fn()} ChatImpl={Chat} startPolling={okPolling} />);
  await screen.findByTestId("fake-chat");
  fireEvent.click(screen.getByRole("button", { name: "B" }));
  const crashAndRestart = async () => {
    fireEvent.click(screen.getByRole("button", { name: "crash" }));
    fireEvent.click(await screen.findByRole("button", { name: "Перезапустить чат" }));
    await screen.findByTestId("fake-chat");
  };
  await crashAndRestart();                                                         // первый сбой — с B
  await waitFor(() => expect(mounts).toEqual([undefined, "ses_B"]));
  await crashAndRestart();                                                         // второй подряд — новый разговор
  await waitFor(() => expect(mounts).toEqual([undefined, "ses_B", undefined]));
  fireEvent.click(screen.getByRole("button", { name: "B" }));                     // студент сам открыл B — и снова сбой
  await crashAndRestart();
  await waitFor(() => expect(mounts).toEqual([undefined, "ses_B", undefined, undefined]));
  expect(a.report.mock.calls).toEqual([["ui_crash"], ["ui_crash"], ["ui_crash"]]);   // отчёт — на каждый сбой
});

test("crash_outside_boundary_reports_once", async () => {
  // итоговая доводка Д6б, m3: сбой вне ErrorBoundary (здесь — эффект шапки) корень React 19 снимает целиком; обработчик
  // корня — отчёт ui_crash один раз, текст T_UI_CRASH в #root, отметка старта загрузчику (второго отчёта boot_failed нет)
  const logged = vi.spyOn(console, "error").mockImplementation(() => {});
  const started = vi.fn();
  window.__pcbkStarted = started;
  const container = document.createElement("div");
  document.body.append(container);
  try {
    const a = fakeApi({ me: STUDENT, workplace: READY });
    const broken: typeof startStatusPolling = () => { throw new Error("сбой шапки"); };
    const root = mountChat(container, { api: a, navigate: vi.fn(), startPolling: broken,
                                        ChatImpl: (_p: ChatProps) => <div data-testid="fake-chat" /> });
    await waitFor(() => expect(a.report).toHaveBeenCalled());
    expect(a.report.mock.calls).toEqual([["ui_crash"]]);
    expect(container.textContent).toBe(T_UI_CRASH);
    expect(container.querySelector('[role="alert"][data-kind="fail"]')?.textContent).toBe(T_UI_CRASH);
    expect(started).toHaveBeenCalled();
    expect(logged.mock.calls.some(([first]) => String(first).includes("сбой оболочки"))).toBe(true);
    await new Promise((r) => setTimeout(r, 50));
    expect(a.report).toHaveBeenCalledTimes(1);
    root.unmount();
  } finally {
    container.remove();
    delete window.__pcbkStarted;
  }
});

test("crash_in_first_render_marks_started_and_reports_once", async () => {
  // m3: сбой первой отрисовки — до эффектов App, старт загрузчику ещё не отмечен: обработчик корня отмечает его сам
  // (иначе через 20 с boot.js дал бы второй отчёт boot_failed и сменил бы текст). Два сбоя одной фиксации — один отчёт
  vi.spyOn(console, "error").mockImplementation(() => {});
  const started = vi.fn();
  window.__pcbkStarted = started;
  const container = document.createElement("div");
  document.body.append(container);
  try {
    const a = fakeApi({ me: STUDENT, workplace: READY });
    const Broken = () => { useEffect(() => { throw new Error("сбой эффекта"); }, []); return <p>часть</p>; };
    const Shell = (_p: AppProps) => <><Broken /><Broken /></>;
    const root = mountChat(container, { api: a, navigate: vi.fn() }, Shell);
    await waitFor(() => expect(a.report).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    expect(a.report.mock.calls).toEqual([["ui_crash"]]);
    expect(started).toHaveBeenCalledTimes(1);                                      // Shell старт не отмечает — отметил корень
    expect(container.textContent).toBe(T_UI_CRASH);
    root.unmount();
  } finally {
    container.remove();
    delete window.__pcbkStarted;
  }
});

test("restart_after_archive_opens_new", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const { Chat, mounts } = crashableChat();
  render(<App api={fakeApi({ me: STUDENT, workplace: READY })} navigate={vi.fn()} ChatImpl={Chat} startPolling={okPolling} />);
  await screen.findByTestId("fake-chat");
  fireEvent.click(screen.getByRole("button", { name: "B" }));
  fireEvent.click(screen.getByRole("button", { name: "archived" }));              // открытый ушёл в архив — чат на новом
  fireEvent.click(screen.getByRole("button", { name: "crash" }));
  fireEvent.click(await screen.findByRole("button", { name: "Перезапустить чат" }));
  await screen.findByTestId("fake-chat");
  await waitFor(() => expect(mounts).toEqual([undefined, undefined]));
});

// --- доводка 1, m2: сверка имени — только для того разговора, что открыт, когда пришёл ответ списка ---

function fakeTitleRuntime(main: { id: string; title: string }) {
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  const state = { ...main };
  const runtime = {
    threads: {
      getLoadThreadsPromise: vi.fn(() => held),
      reload: vi.fn(async () => {}),
      mainItem: { getState: () => ({ id: state.id, remoteId: state.id, externalId: state.id, title: state.title }) },
    },
  } as unknown as AssistantRuntime;
  return { runtime, state, release, reload: (runtime.threads.reload as ReturnType<typeof vi.fn>) };
}

test("title_follow_skips_when_other_thread_opened", async () => {
  const f = fakeTitleRuntime({ id: "ses_1", title: "Старое" });
  renderHook(() => useTitleFollowsSession(f.runtime, { id: "ses_1", title: "Новое" }));
  Object.assign(f.state, { id: "ses_2", title: "Другой" });                       // пока список в пути, открыли другой
  await act(async () => { f.release(); });
  expect(f.reload).not.toHaveBeenCalled();                                         // имя ses_1 к ses_2 не относится
  const g = fakeTitleRuntime({ id: "ses_1", title: "Старое" });                    // положительный контроль
  renderHook(() => useTitleFollowsSession(g.runtime, { id: "ses_1", title: "Новое" }));
  await act(async () => { g.release(); });
  expect(g.reload).toHaveBeenCalledTimes(1);
});

test("title_follow_waits_for_list_in_flight", async () => {
  // m1 на хуке: имя в списке уже совпадает, но в пути ответ списка, запрошенный раньше, — сверка после него
  const f = fakeTitleRuntime({ id: "ses_1", title: "Новое" });
  renderHook(() => useTitleFollowsSession(f.runtime, { id: "ses_1", title: "Новое" }));
  f.state.title = "Старое";                                                        // ответ списка затёр имя
  await act(async () => { f.release(); });
  expect(f.reload).toHaveBeenCalledTimes(1);
});
