// Отображение разговора (Д6-R41, R42): только разрешённые кнопки, разметка модели инертна, строка вызова
// инструмента, ошибка сообщения, висящие разрешения и вопросы — только «Отклонить».
// runtime — useExternalStoreRuntime({ messages, isRunning, onNew, convertMessage }), без OpenCode и сети.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useExternalStoreRuntime, type ThreadMessageLike } from "@assistant-ui/react";
import { afterEach, expect, test, vi } from "vitest";
import { Chat } from "../src/components/Chat";
import { ChatView } from "../src/components/ChatView";
import { PendingInteractions } from "../src/components/PendingInteractions";
import { MAX_TEXT, T_TOO_MANY_TABS } from "../src/texts";

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

function stubGateway(answer: (method: string, path: string, body: string) => Response | undefined) {
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
function stubOpenSession(answer: (method: string, path: string, body: string) => Response | undefined) {
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
