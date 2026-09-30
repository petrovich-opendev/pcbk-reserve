// Отображение разговора (Д6-R41, R42): только разрешённые кнопки, разметка модели инертна, строка вызова
// инструмента, ошибка сообщения, висящие разрешения и вопросы — только «Отклонить».
// runtime — useExternalStoreRuntime({ messages, isRunning, onNew, convertMessage }), без OpenCode и сети.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useExternalStoreRuntime, type ThreadMessageLike } from "@assistant-ui/react";
import { afterEach, expect, test, vi } from "vitest";
import { Chat } from "../src/components/Chat";
import { ChatView } from "../src/components/ChatView";
import { PendingInteractions } from "../src/components/PendingInteractions";
import { MAX_TEXT } from "../src/texts";

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
    "[внешняя](https://example.org/a) [страница](/status) [якорь](#x) [данные](data:text/html,x) [файл](file:///etc/passwd)")]} />);
  const href = (name: string) => screen.getByText(name).closest("a")!.getAttribute("href");
  expect(href("внешняя")).toBe("https://example.org/a");
  expect(href("страница")).toBe("/status");
  expect(href("якорь")).toBe("#x");
  expect(href("данные")).toBeNull();
  expect(href("файл")).toBeNull();
  for (const a of container.querySelectorAll("a")) {
    expect(a.getAttribute("rel")).toBe("noopener noreferrer");
    expect(a.getAttribute("target")).toBe("_blank");
  }
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
