// Отображение разговора (Д6-R41, R42): только разрешённые кнопки, разметка модели инертна, строка вызова
// инструмента, ошибка сообщения, висящие разрешения и вопросы — только «Отклонить».
// runtime — useExternalStoreRuntime({ messages, isRunning, onNew, convertMessage }), без OpenCode и сети.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useExternalStoreRuntime, type AssistantRuntime, type ThreadMessageLike } from "@assistant-ui/react";
import { afterEach, expect, test, vi } from "vitest";
import { Chat } from "../src/components/Chat";
import { ChatView } from "../src/components/ChatView";
import { PendingInteractions } from "../src/components/PendingInteractions";
import { MAX_TEXT, T_TOO_MANY_TABS, T_TURN_SILENT } from "../src/texts";
import { RESYNC_S } from "../src/watch";

// адаптер OpenCode — настоящий; useOpenCodeRuntime лишь отдаёт тесту рантайм чата (итоговая доводка Д6б, Б3-m1)
const seen = vi.hoisted(() => ({ runtime: null as unknown }));
vi.mock("@assistant-ui/react-opencode", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@assistant-ui/react-opencode")>();
  return {
    ...mod,
    useOpenCodeRuntime: (o: Parameters<typeof mod.useOpenCodeRuntime>[0]) => {
      const runtime = mod.useOpenCodeRuntime(o);
      seen.runtime = runtime;
      return runtime;
    },
  };
});

type Status = NonNullable<ThreadMessageLike["status"]>;
const DONE: Status = { type: "complete", reason: "stop" };
let seq = 0;
const id = (): string => `m${++seq}`;

const user = (text: string): ThreadMessageLike => ({ id: id(), role: "user", content: [{ type: "text", text }] });
const assistant = (text: string): ThreadMessageLike =>
  ({ id: id(), role: "assistant", content: [{ type: "text", text }], status: DONE });
// state: "complete" — вывод есть; "error" — отказ инструмента; "running" — ход идёт, вывода ещё нет
const assistantTool = (toolName: string, args: Record<string, string[]>, output: string,
                       state: "complete" | "error" | "running"): ThreadMessageLike => ({
  id: id(), role: "assistant",
  content: [{ type: "tool-call", toolCallId: `call_${seq}`, toolName, args,
              ...(state === "running" ? {} : { result: output }), ...(state === "error" ? { isError: true } : {}) }],
  status: state === "running" ? { type: "running" } : DONE,
});
const assistantError = (text: string): ThreadMessageLike =>
  ({ id: id(), role: "assistant", content: [], status: { type: "incomplete", reason: "error", error: text } });

function Harness(p: { messages: ThreadMessageLike[]; running?: boolean; streamLine?: string; error?: string }) {
  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages: p.messages, isRunning: p.running ?? false, onNew: async () => {}, convertMessage: (m) => m,
  });
  return <ChatView runtime={runtime} streamLine={p.streamLine} error={p.error} />;
}

test("chat_view_has_only_allowed_controls", () => {
  const { container } = render(<Harness messages={[user("вопрос"), assistant("ответ")]} running />);
  expect(screen.getByRole("button", { name: "Стоп" })).toBeTruthy();
  for (const name of [/Удалить/, /Изменить/, /Повторить/, /Ветка/, /Из архива/, /Прикрепить/, /Копировать/])
    expect(screen.queryByRole("button", { name })).toBeNull();
  expect(container.querySelector('input[type="file"]')).toBeNull();
  expect(container.querySelector("textarea")!.getAttribute("maxlength")).toBe(String(MAX_TEXT));
  const input = screen.getByRole("textbox", { name: "Ваш вопрос агенту" });   // подпись для чтения с экрана
  expect(input.getAttribute("placeholder")).toBe("Ваш вопрос агенту");
  expect(input.getAttribute("name")).toBe("question");
  // кнопок ровно две: «Стоп» и «Отправить» не видна, пока ход идёт
  expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Стоп"]);
});

test("send_button_when_idle", () => {
  render(<Harness messages={[user("вопрос"), assistant("ответ")]} />);
  expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Отправить"]);
  expect(screen.getByText("вопрос")).toBeTruthy();
  expect(screen.getByText("ответ")).toBeTruthy();
});

test("renders_model_markup_inert", () => {
  const { container } = render(<Harness messages={[assistant(
    '<img src=x onerror="alert(1)"> ![схема](/pcbk/api/workplace) [клик](javascript:alert(1)) **ok**\n\n' +
    "<script>alert(1)</script>")]} />);
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("script")).toBeNull();
  expect(screen.getByText(/\[картинка: схема\]/)).toBeTruthy();
  expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  expect(container.querySelector("strong")!.textContent).toBe("ok");
  expect(container.textContent).toContain('<img src=x onerror="alert(1)">');   // HTML — текстом
  expect(container.textContent).toContain("<script>alert(1)</script>");
});

test("links_keep_only_safe_href", () => {
  const { container } = render(<Harness messages={[assistant(
    "[внешняя](https://example.org/a) [страница](/status) [якорь](#x) [данные](data:text/html,x) [файл](file:///etc/passwd) " +
    "[почта](mailto:a@b.c) [без схемы](//evil.example/x) [косая](/\\evil.example/x) текст[^1]\n\n[^1]: сноска")]} />);
  const link = (name: string) => screen.getByText(name).closest("a")!;
  expect(link("внешняя").getAttribute("href")).toBe("https://example.org/a");
  expect(link("страница").getAttribute("href")).toBe("/status");
  expect(link("якорь").getAttribute("href")).toBe("#x");
  for (const name of ["данные", "файл", "почта", "без схемы"])
    expect(link(name).getAttribute("href"), name).toBeNull();          // чужая схема или хост без схемы
  expect(link("косая").getAttribute("href")).toBe("/%5Cevil.example/x");  // «\» markdown кодирует — путь своего сайта
  for (const a of container.querySelectorAll("a")) expect(a.getAttribute("rel")).toBe("noopener noreferrer");
  expect(link("внешняя").getAttribute("target")).toBe("_blank");
  expect(link("страница").getAttribute("target")).toBe("_blank");
  // якорь и сноски GFM (ссылка и возврат) — на этой же странице, без второй копии чата
  const anchors = [...container.querySelectorAll('a[href^="#"]')];
  expect(anchors.length).toBe(3);
  for (const a of anchors) expect(a.getAttribute("target"), a.getAttribute("href")!).toBeNull();
});

test("tool_line_and_truncated_output", () => {
  render(<Harness messages={[assistantTool("pcbk_tag_now", { tags: ["T1"] }, "x".repeat(10_000), "complete")]} />);
  expect(screen.getByText("Запрос к данным: текущее значение — готово")).toBeTruthy();
  const details = document.querySelector("details")!;
  expect(details.open).toBe(false);                                          // свёрнут
  const output = document.querySelector('details pre[data-part="output"]')!.textContent!;
  expect(output.length).toBe(2000 + 1);                                      // 2 000 знаков и «…»
  expect(output.startsWith("x".repeat(2000))).toBe(true);
  expect(document.querySelector('details pre[data-part="args"]')!.textContent).toContain('"T1"');
});

test("tool_line_states_and_names", () => {
  render(<Harness messages={[
    assistantTool("pcbk_tag_period", { tags: ["T1"] }, "", "running"),
    user("ещё"),
    assistantTool("pcbk_catalog_search", { tags: ["T1"] }, "нет доступа", "error"),
    user("и ещё"),
    assistantTool("other_tool", { tags: ["T1"] }, "короткий вывод", "complete"),
  ]} />);
  expect(screen.getByText("Запрос к данным: сводка за период — идёт…")).toBeTruthy();
  expect(screen.getByText("Запрос к данным: поиск тегов — ошибка")).toBeTruthy();
  expect(screen.getByText("Запрос к данным: other_tool — готово")).toBeTruthy();
  const outputs = [...document.querySelectorAll('pre[data-part="output"]')].map((p) => p.textContent);
  expect(outputs).toEqual(["нет доступа", "короткий вывод"]);                 // у идущего вывода нет
});

test("tool_output_markup_is_text", () => {
  const { container } = render(<Harness messages={[
    assistantTool("pcbk_tag_now", { tags: ["<img src=x onerror=alert(1)>"] }, '<script>alert(1)</script><img src=x>', "complete")]} />);
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector('pre[data-part="output"]')!.textContent).toBe("<script>alert(1)</script><img src=x>");
});

test("message_error_visible", () => {
  render(<Harness messages={[assistantError("На счёте OpenRouter кончились деньги — модель не отвечает. Сообщите преподавателю.")]} />);
  expect(screen.getByText(/кончились деньги/)).toBeTruthy();
});

test("stream_and_error_lines", () => {
  render(<Harness messages={[user("вопрос")]} streamLine="Связь с рабочим местом прервалась — переподключаюсь через 4 с"
                  error="Вызов не разрешён" />);
  expect(screen.getByText("Связь с рабочим местом прервалась — переподключаюсь через 4 с")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toBe("Вызов не разрешён");
});

test("pending_interactions_reject_only", () => {
  const onRejectPermission = vi.fn();
  render(<PendingInteractions permissions={[{ id: "per_1", permission: "doom_loop" }]} questions={[]} onRejectPermission={onRejectPermission} onRejectQuestion={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /Разрешить/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Отклонить" }));
  expect(onRejectPermission).toHaveBeenCalledWith("per_1");
});

test("pending_question_reject", () => {
  const onRejectQuestion = vi.fn();
  render(<PendingInteractions permissions={[]} questions={[{ id: "que_1" }]} onRejectPermission={vi.fn()} onRejectQuestion={onRejectQuestion} />);
  expect(screen.getByText("Агент ждёт ответа, которого этот чат пока не умеет дать")).toBeTruthy();
  expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Отклонить"]);
  fireEvent.click(screen.getByRole("button", { name: "Отклонить" }));
  expect(onRejectQuestion).toHaveBeenCalledWith("que_1");
});

test("pending_nothing_when_empty", () => {
  const { container } = render(<PendingInteractions permissions={[]} questions={[]} onRejectPermission={vi.fn()} onRejectQuestion={vi.fn()} />);
  expect(container.innerHTML).toBe("");
});

test("reasoning_not_shown", () => {
  render(<Harness messages={[{ id: id(), role: "assistant", status: DONE,
    content: [{ type: "reasoning", text: "ход мысли модели" }, { type: "text", text: "ответ" }] }]} />);
  expect(screen.queryByText(/ход мысли модели/)).toBeNull();
  expect(screen.getByText("ответ")).toBeTruthy();
});

test("user_text_plain_not_markdown", () => {
  const { container } = render(<Harness messages={[user("**не жирный**\nвторая строка <b>x</b>")]} />);
  expect(container.querySelector("strong, b")).toBeNull();
  expect(container.querySelector(".plain")!.textContent).toBe("**не жирный**\nвторая строка <b>x</b>");
});

// --- Chat: клиент OpenCode, обёртка шлюза и рантайм на монтирование (сеть — подменённый fetch) ---
// Поле ввода — по роли: react-textarea-autosize держит в body свою скрытую textarea для замера (aria-hidden).

const SES = "ses_0123456789abcdefghijklmnop";
const ocJson = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

type Answer = Response | Promise<Response> | undefined;      // ответ можно придержать промисом

function stubGateway(answer: (method: string, path: string, body: string) => Answer) {
  const calls: { method: string; path: string; body: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (r: Request) => {
    const path = new URL(r.url).pathname;
    const body = r.body ? await r.clone().text() : "";
    calls.push({ method: r.method, path, body });
    return answer(r.method, path, body) ?? ocJson(404, { name: "NotFound", data: { message: "нет" } });
  }));
  return calls;
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test("chat_halts_once_on_signed_out", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});      // адаптер пишет сбой списка разговоров в консоль
  const calls = stubGateway(() => ocJson(401, { name: "Unauthorized", data: { message: "Нужен вход" } }));
  const onHalt = vi.fn();
  render(<Chat onHalt={onHalt} />);
  expect(screen.getByRole("button", { name: "Новый разговор" })).toBeTruthy();
  await waitFor(() => expect(onHalt).toHaveBeenCalledWith("signed_out"));
  const seen = calls.length;
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "вопрос" } });
  const send = screen.getByRole("button", { name: "Отправить" }) as HTMLButtonElement;
  expect(send.disabled).toBe(false);                                // отправка правда пробует уйти
  fireEvent.click(send);
  await new Promise((r) => setTimeout(r, 100));
  expect(calls.length).toBe(seen);                                  // после остановки — ни одного запроса
  expect(onHalt).toHaveBeenCalledTimes(1);
  expect(calls.every((c) => c.path.startsWith("/pcbk/api/oc/"))).toBe(true);
  expect(screen.queryByRole("alert")).toBeNull();                   // остановка — не ошибка в строке
});

test("chat_sends_text_only_and_reports_thread", async () => {
  const session = { id: SES, slug: "s", projectID: "p", directory: "/work", title: "", version: "1.18.33",
                    time: { created: 1, updated: 1 } };
  const calls = stubGateway((method, path) => {
    if (path === "/pcbk/api/oc/experimental/session") return ocJson(200, []);
    if (method === "POST" && path === "/pcbk/api/oc/session") return ocJson(200, session);
    if (path === `/pcbk/api/oc/session/${SES}`) return ocJson(200, session);
    if (path === `/pcbk/api/oc/session/${SES}/message`) return ocJson(200, []);
    if (path.endsWith("/prompt_async")) return new Response(null, { status: 204 });
    if (path.endsWith("/summarize")) return ocJson(200, true);
    return undefined;
  });
  const onThreadIdChange = vi.fn();
  render(<Chat onHalt={vi.fn()} onThreadIdChange={onThreadIdChange} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Какое значение у T1?" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  await waitFor(() => expect(calls.some((c) => c.path.endsWith("/prompt_async"))).toBe(true));
  const prompt = calls.find((c) => c.path.endsWith("/prompt_async"))!;
  expect(prompt.path).toBe(`/pcbk/api/oc/session/${SES}/prompt_async`);
  expect(JSON.parse(prompt.body)).toEqual({ parts: [{ type: "text", text: "Какое значение у T1?" }] });  // без agent и model
  await waitFor(() => expect(onThreadIdChange).toHaveBeenCalledWith(SES));
});

test("chat_shows_refusal_text", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  stubGateway((method, path) => {
    if (path === "/pcbk/api/oc/experimental/session") return ocJson(200, []);
    if (method === "POST" && path === "/pcbk/api/oc/session")
      return ocJson(403, { name: "Forbidden", data: { message: "Вызов не разрешён на стенде" } });
    return undefined;
  });
  const onHalt = vi.fn();
  render(<Chat onHalt={onHalt} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Вызов не разрешён на стенде");
  expect(onHalt).not.toHaveBeenCalled();
});

// Открытый разговор (initialSessionId): список, сессия и история отвечают, прочее решает тест.
const SESSION = { id: SES, slug: "s", projectID: "p", directory: "/work", title: "", version: "1.18.33",
                  time: { created: 1, updated: 1 } };
function stubOpenSession(answer: (method: string, path: string, body: string) => Answer) {
  return stubGateway((method, path, body) => {
    const own = answer(method, path, body);
    if (own !== undefined) return own;
    if (path === "/pcbk/api/oc/experimental/session") return ocJson(200, [SESSION]);
    if (path === `/pcbk/api/oc/session/${SES}`) return ocJson(200, SESSION);
    if (path === `/pcbk/api/oc/session/${SES}/message`) return ocJson(200, []);
    return undefined;
  });
}
// поток /event: отдаёт события и остаётся открытым (конец — отменой при размонтировании)
function eventStream(events: object[]): Response {
  const enc = new TextEncoder();
  return new Response(new ReadableStream({
    start(c) { for (const e of events) c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); },
  }), { status: 200, headers: { "content-type": "text/event-stream" } });
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

test("chat_rejects_pending_through_adapter", async () => {
  const calls = stubOpenSession((method, path) => {
    if (path === "/pcbk/api/oc/event") return eventStream([
      { type: "server.connected", properties: {} },
      { type: "permission.asked", properties: { id: "per_1", sessionID: SES, permission: "doom_loop", patterns: [], metadata: {}, always: [] } },
      { type: "question.asked", properties: { id: "que_1", sessionID: SES, questions: [{ question: "Какой тег?", header: "Тег", options: [] }] } },
    ]);
    if (method === "POST" && (path.endsWith("/reply") || path.endsWith("/reject"))) return ocJson(200, true);
    return undefined;
  });
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES} />);
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Отклонить" })).toHaveLength(2));
  fireEvent.click(container.querySelector('li[data-permission="doom_loop"] button')!);
  fireEvent.click(container.querySelector(".pending li:not([data-permission]) button")!);
  await waitFor(() => expect(screen.queryAllByRole("button", { name: "Отклонить" })).toHaveLength(0));
  const posts = calls.filter((c) => c.method === "POST").map((c) => [c.path, c.body ? JSON.parse(c.body) : null]);
  expect(posts).toContainEqual(["/pcbk/api/oc/permission/per_1/reply", { reply: "reject" }]);   // только отклонение
  expect(posts.some(([path]) => path === "/pcbk/api/oc/question/que_1/reject")).toBe(true);
  expect(posts.filter(([path]) => String(path).startsWith("/pcbk/api/oc/permission/"))).toHaveLength(1);
});

test("chat_stream_line_from_gateway", async () => {
  stubOpenSession((_method, path) => path === "/pcbk/api/oc/event"
    ? ocJson(429, { name: "TooManyRequests", data: { message: "Открыто слишком много вкладок чата — закройте лишние" } })
    : undefined);
  render(<Chat onHalt={vi.fn()} initialSessionId={SES} />);
  expect((await screen.findByRole("status")).textContent).toBe(T_TOO_MANY_TABS);
  expect(screen.queryByRole("alert")).toBeNull();                   // поток — строкой потока, не ошибкой
});

test.each([
  [401, "Unauthorized", "signed_out"],
  [503, "WorkplaceNotReady", "asleep"],
] as const)("chat_halt_is_not_an_error_line_%s", async (status, name, kind) => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  stubGateway((method, path) => {
    if (path === "/pcbk/api/oc/experimental/session") return ocJson(200, []);
    if (method === "POST" && path === "/pcbk/api/oc/session") return ocJson(status, { name, data: { message: "текст шлюза" } });
    return undefined;
  });
  const onHalt = vi.fn();
  render(<Chat onHalt={onHalt} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  await waitFor(() => expect(onHalt).toHaveBeenCalledWith(kind));
  await sleep(50);                                                  // ошибка вызова доходит до onError после остановки
  expect(screen.queryByRole("alert")).toBeNull();                   // остановку показывает App, не строка ошибки
});

test("chat_error_line_clears_on_new_run", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  let prompts = 0;
  stubOpenSession((_method, path) => {
    if (path.endsWith("/prompt_async")) {
      prompts += 1;
      return prompts === 1 ? ocJson(403, { name: "Forbidden", data: { message: "Вызов не разрешён на стенде" } })
        : new Response(null, { status: 204 });
    }
    return undefined;
  });
  const onThreadIdChange = vi.fn();
  render(<Chat onHalt={vi.fn()} initialSessionId={SES} onThreadIdChange={onThreadIdChange} />);
  await waitFor(() => expect(onThreadIdChange).toHaveBeenCalledWith(SES));   // разговор открыт — дальше id не меняется
  const ask = async (text: string) => {
    fireEvent.change(screen.getByRole("textbox"), { target: { value: text } });
    const send = await screen.findByRole("button", { name: "Отправить" });
    await waitFor(() => expect((send as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(send);
  };
  await ask("первый");
  expect((await screen.findByRole("alert")).textContent).toBe("Вызов не разрешён на стенде");
  await ask("второй");
  await waitFor(() => expect(prompts).toBe(2));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());      // новый ход — прежней ошибки нет
  expect(onThreadIdChange).toHaveBeenCalledTimes(1);
});

test("chat_client_once_per_mount", async () => {
  const calls = stubOpenSession(() => undefined);
  const { rerender } = render(<Chat onHalt={vi.fn()} />);
  await waitFor(() => expect(calls.filter((c) => c.path === "/pcbk/api/oc/experimental/session")).toHaveLength(1));
  rerender(<Chat onHalt={vi.fn()} onThreadIdChange={vi.fn()} />);   // новые колбэки — тот же клиент и рантайм
  rerender(<Chat onHalt={vi.fn()} />);
  await sleep(100);
  expect(calls.filter((c) => c.path === "/pcbk/api/oc/experimental/session")).toHaveLength(1);
});

// --- Д6б: список слева, молчание хода и потеря связи (R51, R52; preflight Ф11, Ф13) ---

// поток /event, в который тест дописывает события сам (после того как разговор загрузился)
function controlledEvents() {
  const enc = new TextEncoder();
  let ctl: ReadableStreamDefaultController<Uint8Array> | null = null;
  const response = () => new Response(new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
      c.enqueue(enc.encode(`data: ${JSON.stringify({ type: "server.connected", properties: {} })}\n\n`));
    },
    cancel() { ctl = null; },
  }), { status: 200, headers: { "content-type": "text/event-stream" } });
  const push = (...events: object[]) => { for (const e of events) ctl?.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); };
  return { response, push };
}

test("chat_sidebar_title_follows_session", async () => {
  // Ф13: имя открытого разговора — из useOpenCodeSession; адаптер кладёт session.updated в разговор, а не в список,
  // поэтому список перечитывается при смене имени — один раз, а не на каждое session.updated (limit_req 20 r/s)
  let lists = 0;
  const named = (title: string, updated = 1) => ({ ...SESSION, title, time: { created: 1, updated } });
  const events = controlledEvents();
  stubOpenSession((_method, path) => {
    if (path === "/pcbk/api/oc/experimental/session") {
      lists += 1;
      return ocJson(200, [named(lists === 1 ? "New session - 2026-10-01" : "Значение T1")]);
    }
    if (path === `/pcbk/api/oc/session/${SES}`) return ocJson(200, named("New session - 2026-10-01"));
    if (path === "/pcbk/api/oc/event") return events.response();
    return undefined;
  });
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES} />);
  const row = () => container.querySelector(`[data-session-id="${SES}"]`)?.textContent ?? "";
  await waitFor(() => expect(row()).toContain("New session - 2026-10-01"));
  await sleep(100);
  expect(lists).toBe(1);                                                    // имена сходятся — список не перечитан
  events.push({ type: "session.updated", properties: { info: named("Значение T1", 2) } });     // самозаголовок OpenCode
  await waitFor(() => expect(row()).toContain("Значение T1"));
  expect(lists).toBe(2);
  events.push({ type: "session.updated", properties: { info: named("Значение T1", 3) } },      // то же имя — не перечитывать
              { type: "session.updated", properties: { info: named("Значение T1", 4) } });
  await sleep(150);
  expect(lists).toBe(2);
});

// видимость вкладки в jsdom: своё свойство поверх геттера документа
function setVisibility(state: "visible" | "hidden"): void {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

test("chat_reports_turn_silent_once", async () => {
  // Ф11: начало хода — isRunning false → true (session.status busy), часы — Date.now; вкладка в фоне
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  stubOpenSession((_method, path) => path === "/pcbk/api/oc/event" ? eventStream([
    { type: "server.connected", properties: {} },
    { type: "session.status", properties: { sessionID: SES, status: { type: "busy" } } },
  ]) : undefined);
  const onReport = vi.fn();
  render(<Chat onHalt={vi.fn()} initialSessionId={SES} onReport={onReport} />);
  await screen.findByRole("button", { name: "Стоп" });                        // ход идёт
  try {
    setVisibility("hidden");
    now += 181_000;                                                           // 3 мин без прироста ответа, тиков не было
    await sleep(1_500);                                                       // тик увидел разрыв — отсрочка (m4)
    expect(onReport).not.toHaveBeenCalled();
    now += RESYNC_S * 1000;                                                   // адаптер успел бы перечитать разговор
    await waitFor(() => expect(onReport).toHaveBeenCalledWith("turn_silent"), { timeout: 3000 });   // тик раз в секунду
    expect(await screen.findByText(T_TURN_SILENT)).toBeTruthy();
    await sleep(1_500);
    expect(onReport).toHaveBeenCalledTimes(1);                                // один на ход
  } finally {
    delete (document as { visibilityState?: unknown }).visibilityState;
  }
});

test("chat_reports_stream_down_after_long_outage", async () => {
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  let events = 0;
  stubOpenSession((_method, path) => {
    if (path !== "/pcbk/api/oc/event") return undefined;
    events += 1;
    if (events === 1) return ocJson(503, { name: "Unavailable", data: { message: "Серверный слой не отвечает" } });
    now += 130_000;                                                           // связи не было 130 с
    return eventStream([{ type: "server.connected", properties: {} }]);
  });
  const onReport = vi.fn();
  render(<Chat onHalt={vi.fn()} initialSessionId={SES} onReport={onReport} />);
  await waitFor(() => expect(onReport).toHaveBeenCalledWith("stream_down"), { timeout: 4000 });   // при восстановлении
  await sleep(100);
  expect(onReport).toHaveBeenCalledTimes(1);
  expect(events).toBe(2);
});

test("chat_no_reports_after_halt", async () => {
  // без сессии отчёты не шлются: место уснуло посреди хода (503 WorkplaceNotReady на «Стоп») — чат остановлен,
  // сторож хода дотикал до 3 минут, а отчёта нет (App снимет чат, но до того отчёт уже ушёл бы впустую)
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  vi.spyOn(console, "error").mockImplementation(() => {});
  stubOpenSession((method, path) => {
    if (path === "/pcbk/api/oc/event") return eventStream([
      { type: "server.connected", properties: {} },
      { type: "session.status", properties: { sessionID: SES, status: { type: "busy" } } },
    ]);
    if (method === "POST" && path.endsWith("/abort"))
      return ocJson(503, { name: "WorkplaceNotReady", data: { message: "Рабочее место спит" } });
    return undefined;
  });
  const onReport = vi.fn(); const onHalt = vi.fn();
  render(<Chat onHalt={onHalt} initialSessionId={SES} onReport={onReport} />);
  fireEvent.click(await screen.findByRole("button", { name: "Стоп" }));
  await waitFor(() => expect(onHalt).toHaveBeenCalledWith("asleep"));
  try {
    setVisibility("hidden");
    now += 181_000;
    await sleep(1_500);                                                       // тик увидел разрыв — отсрочка
    now += RESYNC_S * 1000;
    await sleep(1_500);                                                       // тик после отсрочки — ход всё ещё «идёт»
    expect(onReport).not.toHaveBeenCalled();
  } finally {
    delete (document as { visibilityState?: unknown }).visibilityState;
  }
});

// --- доводка 1 ---

test.each([
  ["archived", [{ ...SESSION, time: { created: 1, updated: 1, archived: 1_700_000_000_000 } }]],
  ["gone", []],
] as const)("chat_initial_session_not_listed_opens_new_%s", async (_name, listed) => {
  // I1: switchToThread библиотеки снял бы архив (PATCH time.archived = null, против R52). Разговор не в неархивном
  // списке — новый разговор, ни одного PATCH, App забывает прежний
  const archivedSession = { ...SESSION, time: { created: 1, updated: 1, archived: 1_700_000_000_000 } };
  const calls = stubOpenSession((_method, path) => {
    if (path === "/pcbk/api/oc/experimental/session") return ocJson(200, listed);
    if (path === `/pcbk/api/oc/session/${SES}`) return ocJson(200, archivedSession);
    return undefined;
  });
  const onThreadIdChange = vi.fn();
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES} onThreadIdChange={onThreadIdChange} />);
  await waitFor(() => expect(onThreadIdChange).toHaveBeenCalledWith(undefined));
  await sleep(150);
  expect(calls.filter((c) => c.method === "PATCH")).toEqual([]);
  expect(calls.some((c) => c.body.includes('"archived":null'))).toBe(false);
  expect(calls.filter((c) => c.path.startsWith(`/pcbk/api/oc/session/${SES}`))).toEqual([]);   // и не открыт
  expect(onThreadIdChange.mock.calls).toEqual([[undefined]]);
  expect(container.querySelector(`[data-session-id="${SES}"]`)).toBeNull();
  expect(screen.getByRole("button", { name: "Новый разговор" }).getAttribute("aria-current")).toBe("true");
});

test("chat_initial_session_waits_for_list", async () => {
  // список не загрузился — неизвестно, в архиве ли разговор: не открывать и не забывать
  vi.spyOn(console, "error").mockImplementation(() => {});
  const calls = stubOpenSession((_method, path) =>
    path === "/pcbk/api/oc/experimental/session" ? ocJson(500, { name: "UnknownError", data: { message: "сбой" } }) : undefined);
  const onThreadIdChange = vi.fn();
  render(<Chat onHalt={vi.fn()} initialSessionId={SES} onThreadIdChange={onThreadIdChange} />);
  await waitFor(() => expect(calls.some((c) => c.path === "/pcbk/api/oc/experimental/session")).toBe(true));
  await sleep(150);
  expect(onThreadIdChange).not.toHaveBeenCalled();
  expect(calls.filter((c) => c.path.startsWith(`/pcbk/api/oc/session/${SES}`) || c.method === "PATCH")).toEqual([]);
});

test("chat_archive_open_thread_forgets_it", async () => {
  // I1: архив открытого разговора — чат на новом, App получает undefined (перезапуск откроет новый)
  const calls = stubOpenSession((method, path) =>
    method === "PATCH" && path === `/pcbk/api/oc/session/${SES}` ? ocJson(200, { ...SESSION, time: { created: 1, updated: 2, archived: 2 } }) : undefined);
  const onThreadIdChange = vi.fn();
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES} onThreadIdChange={onThreadIdChange} />);
  await waitFor(() => expect(onThreadIdChange).toHaveBeenCalledWith(SES));
  const row = container.querySelector<HTMLElement>(`[data-session-id="${SES}"]`)!;
  fireEvent.click(within(row).getByRole("button", { name: "В архив" }));
  await waitFor(() => expect(onThreadIdChange.mock.calls.at(-1)).toEqual([undefined]));
  await waitFor(() => expect(container.querySelector(`[data-session-id="${SES}"]`)).toBeNull());
  const patches = calls.filter((c) => c.method === "PATCH").map((c) => JSON.parse(c.body));
  expect(patches).toHaveLength(1);
  expect(typeof patches[0].time.archived).toBe("number");                      // в архив, а не «из архива»
});

test("chat_title_race_waits_for_list_in_flight", async () => {
  // m1: ответ списка придержан, приходит новое имя — новый запрос списка не уходит, пока прежний в пути; прежний
  // отдаёт старое имя — тогда перечитывание, и в строке новое имя
  let lists = 0;
  let release: (r: Response) => void = () => {};
  const named = (title: string, updated = 1) => ({ ...SESSION, title, time: { created: 1, updated } });
  const events = controlledEvents();
  stubOpenSession((_method, path) => {
    if (path === "/pcbk/api/oc/experimental/session") {
      lists += 1;
      if (lists === 1) return ocJson(200, [named("Имя A")]);
      if (lists === 2) return new Promise<Response>((resolve) => { release = resolve; });   // придержан
      return ocJson(200, [named("Имя C")]);
    }
    if (path === `/pcbk/api/oc/session/${SES}`) return ocJson(200, named("Имя A"));
    if (path === "/pcbk/api/oc/event") return events.response();
    return undefined;
  });
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES} />);
  const row = () => container.querySelector(`[data-session-id="${SES}"]`)?.textContent ?? "";
  await waitFor(() => expect(row()).toContain("Имя A"));
  await sleep(50);
  events.push({ type: "session.updated", properties: { info: named("Имя B", 2) } });   // перечитывание №2 — в пути
  await waitFor(() => expect(lists).toBe(2));
  events.push({ type: "session.updated", properties: { info: named("Имя C", 3) } });   // имя сменилось ещё раз
  await sleep(150);
  expect(lists).toBe(2);                                                    // пока прежний ответ в пути — нового нет
  release(ocJson(200, [named("Имя B", 2)]));                               // прежний ответ — со старым именем
  await waitFor(() => expect(row()).toContain("Имя C"));
  expect(lists).toBe(3);
});

test("chat_no_turn_silent_while_stream_down", async () => {
  // m4: поток лёг посреди хода — отсчёт хода на паузе: при лежащей связи отчёт не «ответ модели не пришёл»
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  let events = 0;
  const enc = new TextEncoder();
  stubOpenSession((_method, path) => {
    if (path !== "/pcbk/api/oc/event") return undefined;
    events += 1;
    if (events === 1) return new Response(new ReadableStream<Uint8Array>({   // ход пошёл, и поток оборвался
      start(c) {
        for (const e of [{ type: "server.connected", properties: {} },
                         { type: "session.status", properties: { sessionID: SES, status: { type: "busy" } } }])
          c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`));
        c.close();
      },
    }), { status: 200, headers: { "content-type": "text/event-stream" } });
    return ocJson(503, { name: "Unavailable", data: { message: "Серверный слой не отвечает" } });
  });
  const onReport = vi.fn();
  render(<Chat onHalt={vi.fn()} initialSessionId={SES} onReport={onReport} />);
  await screen.findByRole("button", { name: "Стоп" });                     // ход идёт
  await waitFor(() => expect(events).toBeGreaterThanOrEqual(2), { timeout: 8_000 });   // переподключение — 503
  await sleep(100);
  try {
    setVisibility("hidden");                                                // фон: тики по часам
    now += 181_000;
    await sleep(1_500);                                                       // разрыв тиков — отсрочка
    now += RESYNC_S * 1000;
    await sleep(1_500);                                                       // отсрочка прошла — держит пауза
    expect(onReport).not.toHaveBeenCalled();
    expect(screen.queryByText(T_TURN_SILENT)).toBeNull();
  } finally {
    delete (document as { visibilityState?: unknown }).visibilityState;
  }
}, 15_000);

// --- итоговая доводка Д6б ---

const UNAVAILABLE = { name: "Unavailable", data: { message: "Серверный слой не отвечает" } };

test("chat_too_many_tabs_pauses_turn", async () => {
  // Б3-m4: «закройте лишние вкладки» — поток не открыт, прироста ответа не видно: отсчёт хода на паузе, как при лежащей
  // связи (stream_down эта строка при этом не даёт — доводка 1)
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  let events = 0;
  const enc = new TextEncoder();
  stubOpenSession((_method, path) => {
    if (path !== "/pcbk/api/oc/event") return undefined;
    events += 1;
    if (events === 1) return new Response(new ReadableStream<Uint8Array>({   // ход пошёл, и поток кончился
      start(c) {
        for (const e of [{ type: "server.connected", properties: {} },
                         { type: "session.status", properties: { sessionID: SES, status: { type: "busy" } } }])
          c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`));
        c.close();
      },
    }), { status: 200, headers: { "content-type": "text/event-stream" } });
    return ocJson(429, { name: "TooManyRequests", data: { message: T_TOO_MANY_TABS } });   // четвёртая вкладка
  });
  const onReport = vi.fn();
  render(<Chat onHalt={vi.fn()} initialSessionId={SES} onReport={onReport} />);
  await screen.findByRole("button", { name: "Стоп" });                     // ход идёт
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe(T_TOO_MANY_TABS), { timeout: 8_000 });
  try {
    setVisibility("hidden");
    now += 181_000;
    await sleep(1_500);                                                       // разрыв тиков — отсрочка
    now += RESYNC_S * 1000;
    await sleep(1_500);                                                       // отсрочка прошла — держит пауза
    expect(onReport).not.toHaveBeenCalled();
    expect(screen.queryByText(T_TURN_SILENT)).toBeNull();
  } finally {
    delete (document as { visibilityState?: unknown }).visibilityState;
  }
}, 15_000);

test.each([
  ["back_within_resync", false],
  ["still_down_after_resync", true],
] as const)("chat_stream_sleep_%s", async (_name, late) => {
  // m4: связь пропала, машина проспала час; первый тик сторожа потока после сна видит разрыв. Поток вернулся в первые
  // RESYNC_S — это сон, stream_down нет; не вернулся и через RESYNC_S после пробуждения — потеря настоящая, отчёт
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  let events = 0;
  let release: (r: Response) => void = () => {};
  stubOpenSession((_method, path) => {
    if (path !== "/pcbk/api/oc/event") return undefined;
    events += 1;
    if (events === 1) return ocJson(503, UNAVAILABLE);                        // связь пропала
    if (events === 2) return new Promise<Response>((resolve) => { release = resolve; });   // переподключение в пути
    return undefined;
  });
  const onReport = vi.fn();
  render(<Chat onHalt={vi.fn()} initialSessionId={SES} onReport={onReport} />);
  await waitFor(() => expect(events).toBe(2), { timeout: 4_000 });
  now += 3_600_000;                                                           // сон
  await sleep(1_200);                                                         // тик после сна — разрыв
  if (late) {
    now += RESYNC_S * 1000;
    await sleep(1_200);
  }
  release(eventStream([{ type: "server.connected", properties: {} }]));       // поток вернулся
  await sleep(200);
  expect(onReport.mock.calls).toEqual(late ? [["stream_down"]] : []);
}, 15_000);

test("chat_initial_session_opens_after_list_reload", async () => {
  // Б3-m5: список упал при монтировании, библиотека перечитала его позже (событие online) — прежний разговор
  // открывается на первой успешной загрузке; до неё он не открыт и не забыт
  vi.spyOn(console, "error").mockImplementation(() => {});                   // библиотека пишет сбой списка
  let lists = 0;
  const calls = stubOpenSession((_method, path) => {
    if (path !== "/pcbk/api/oc/experimental/session") return undefined;
    lists += 1;
    return lists === 1 ? ocJson(500, { name: "UnknownError", data: { message: "сбой" } }) : ocJson(200, [SESSION]);
  });
  const onThreadIdChange = vi.fn();
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES} onThreadIdChange={onThreadIdChange} />);
  await waitFor(() => expect(lists).toBe(1));
  await sleep(150);
  expect(onThreadIdChange).not.toHaveBeenCalled();
  expect(calls.filter((c) => c.path.startsWith(`/pcbk/api/oc/session/${SES}`))).toEqual([]);
  window.dispatchEvent(new Event("online"));                                  // библиотека перечитывает упавший список
  await waitFor(() => expect(onThreadIdChange).toHaveBeenCalledWith(SES));
  expect(lists).toBe(2);
  await waitFor(() => expect(container.querySelector(`[data-session-id="${SES}"]`)?.getAttribute("aria-current")).toBe("true"));
  expect(onThreadIdChange.mock.calls).toEqual([[SES]]);
  expect(calls.filter((c) => c.method === "PATCH")).toEqual([]);
});

test("chat_initial_session_not_opened_over_new_thread", async () => {
  // Б3-m2: пока грузится список, студент начал новый разговор — прежний поверх него не открывается
  const NEW = "ses_9876543210zyxwvutsrqponm";
  const created = { ...SESSION, id: NEW };
  let release: (r: Response) => void = () => {};
  const calls = stubOpenSession((method, path) => {
    if (path === "/pcbk/api/oc/experimental/session") return new Promise<Response>((resolve) => { release = resolve; });
    if (method === "POST" && path === "/pcbk/api/oc/session") return ocJson(200, created);
    if (path === `/pcbk/api/oc/session/${NEW}`) return ocJson(200, created);
    if (path === `/pcbk/api/oc/session/${NEW}/message`) return ocJson(200, []);
    if (path.endsWith("/prompt_async")) return new Response(null, { status: 204 });
    return undefined;
  });
  const onThreadIdChange = vi.fn();
  render(<Chat onHalt={vi.fn()} initialSessionId={SES} onThreadIdChange={onThreadIdChange} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "вопрос" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  await waitFor(() => expect(calls.some((c) => c.path === `/pcbk/api/oc/session/${NEW}/prompt_async`)).toBe(true));
  await waitFor(() => expect(onThreadIdChange).toHaveBeenCalledWith(NEW));
  release(ocJson(200, [SESSION]));                                             // список пришёл, прежний в нём есть
  await sleep(200);
  expect(calls.filter((c) => c.path.startsWith(`/pcbk/api/oc/session/${SES}`))).toEqual([]);
  expect(onThreadIdChange.mock.calls.flat()).not.toContain(SES);
  expect(onThreadIdChange.mock.calls.flat()).not.toContain(undefined);       // и не забыт: решать нечего
});

test("chat_opens_previous_session_without_unarchive", async () => {
  // Б3-m1: разговор ушёл в архив между сверкой со списком и переключением (другая вкладка) — проверку по списку тест
  // обходит, остаётся один барьер: switchToThread(…, { unarchive: false }). Без него библиотека сняла бы архив
  // (PATCH time.archived = null — против R52)
  seen.runtime = null;
  let release: (r: Response) => void = () => {};
  let archived = false;
  const calls = stubOpenSession((method, path) => {
    if (path === "/pcbk/api/oc/experimental/session") return new Promise<Response>((resolve) => { release = resolve; });
    if (method === "PATCH" && path === `/pcbk/api/oc/session/${SES}`) {
      archived = true;
      return ocJson(200, { ...SESSION, time: { created: 1, updated: 2, archived: 2 } });
    }
    if (method === "GET" && path === `/pcbk/api/oc/session/${SES}`)
      return ocJson(200, archived ? { ...SESSION, time: { created: 1, updated: 2, archived: 2 } } : SESSION);
    return undefined;
  });
  const onThreadIdChange = vi.fn();
  render(<Chat onHalt={vi.fn()} initialSessionId={SES} onThreadIdChange={onThreadIdChange} />);
  await waitFor(() => expect(seen.runtime).not.toBeNull());
  const threads = (seen.runtime as AssistantRuntime).threads;
  const original = threads.switchToThread;
  const options: unknown[] = [];
  threads.switchToThread = async (id, opts) => {                             // в архив — раньше переключения
    options.push(opts);
    await threads.getItemById(id).archive();
    return original(id, opts);
  };
  release(ocJson(200, [SESSION]));                                             // по списку он неархивный
  await waitFor(() => expect(onThreadIdChange).toHaveBeenCalledWith(SES));
  await sleep(150);
  expect(options).toEqual([{ unarchive: false }]);
  const patches = calls.filter((c) => c.method === "PATCH").map((c) => JSON.parse(c.body));
  expect(patches).toHaveLength(1);                                             // только «в архив»
  expect(typeof patches[0].time.archived).toBe("number");
  expect(calls.some((c) => c.body.includes('"archived":null'))).toBe(false);
});
