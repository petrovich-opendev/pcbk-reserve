// Список разговоров слева (Д6-R52, Review Focus 4 Д6б), сбой интерфейса (ErrorBoundary → ui_crash) и отметка
// старта для загрузчика (window.__pcbkStarted, preflight Ф9).
// Список — useRemoteThreadListRuntime с адаптером из памяти: видно, что ушло в адаптер (= в шлюз у OpenCode).
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  AssistantRuntimeProvider, useExternalStoreRuntime, useRemoteThreadListRuntime, type RemoteThreadListAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { StrictMode, useEffect, useLayoutEffect } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { App } from "../src/App";
import type { Api, Reply } from "../src/api";
import type { ChatProps } from "../src/components/Chat";
import { ErrorBoundary } from "../src/components/ErrorBoundary";
import { ThreadSidebar } from "../src/components/ThreadSidebar";
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
    logout: vi.fn(reply([200, { ok: true }])), ackNotice: vi.fn(reply([204, null])),
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
