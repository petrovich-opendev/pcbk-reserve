// Фазы чата (Д6-R39, R42, R44): вход, преподаватель, пробуждение места, «место спит», выход.
// api и ChatImpl подменены; startPolling — заглушка, отдающая вид «ok»; опрос места — pollMs (M11).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { expect, test, vi } from "vitest";
import { App } from "../src/App";
import type { Api, Reply } from "../src/api";
import type { ChatProps } from "../src/components/Chat";
import type { startStatusPolling } from "../src/status";
import { STAND_WARNING, T_NOTICE_UNKNOWN } from "../src/texts";

type Answer = [number, unknown];
type Answers = Answer | Answer[];

// Ответы по ручкам: один ответ — на каждый вызов, список — по очереди (последний повторяется).
function api(o: { me?: Answers; workplace?: Answers; wake?: Answers; logout?: Answers; ackNotice?: Answers; agents?: Answers }) {
  const queue = (a: Answers | undefined): (() => Promise<Reply>) => {
    const list: Answer[] = a === undefined ? [[500, null]] : typeof a[0] === "number" ? [a as Answer] : a as Answer[];
    let i = 0;
    return async () => {
      const [status, body] = list[Math.min(i, list.length - 1)]!;
      i += 1;
      return { status, body };
    };
  };
  return {
    me: vi.fn(queue(o.me)),
    workplace: vi.fn(queue(o.workplace)),
    wake: vi.fn(queue(o.wake)),
    logout: vi.fn(queue(o.logout ?? [200, { ok: true }])),
    ackNotice: vi.fn(queue(o.ackNotice)),
    agents: vi.fn(queue(o.agents)),
    report: vi.fn(async (_kind: string) => {}),
  } satisfies Api;
}

const okPolling: typeof startStatusPolling = (o) => {
  o.onView({ level: "ok", text: "Стенд работает" });
  return () => {};
};

const FakeChat = (p: ChatProps) => <div data-testid="fake-chat" data-session={p.initialSessionId ?? ""} />;

const student = [200, { login: "s", role: "student", place: "03" }] as Answer;
const POLL_MS = 20;

test("signed_out_goes_to_login", async () => {
  const navigate = vi.fn();
  render(<App api={api({ me: [401, { name: "Unauthorized" }] })} navigate={navigate} startPolling={okPolling} />);
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
});

test("me_failure_shows_retry", async () => {
  const a = api({ me: [[503, { name: "Unavailable", data: { message: "Учётные записи недоступны — повторите позже" } }],
                       [200, { login: "s", role: "student", place: "03" }]], workplace: [[200, { state: "ready" }]] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
  expect(await screen.findByText(/Учётные записи недоступны/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await screen.findByTestId("fake-chat");
  expect(a.wake).not.toHaveBeenCalled();                  // место готово — будить нечего
});

test("me_offline_shows_text_and_retry", async () => {
  render(<App api={api({ me: [0, null] })} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
  expect(await screen.findByText("Серверный слой не отвечает")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Повторить" })).toBeTruthy();
});

test("teacher_gets_no_chat_and_no_wake", async () => {
  const a = api({ me: [200, { login: "t", role: "teacher", place: null }] });
  const polling = vi.fn(okPolling);
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={polling} />);
  expect(await screen.findByText(/У преподавателя пока нет чата/)).toBeTruthy();
  expect(a.wake).not.toHaveBeenCalled();
  expect(a.workplace).not.toHaveBeenCalled();
  expect(screen.queryByTestId("fake-chat")).toBeNull();
  expect(screen.getByText("Вы вошли как t")).toBeTruthy();
  await waitFor(() => expect(polling).toHaveBeenCalled());       // опрос стартует в эффекте шапки
  expect(polling.mock.calls.at(-1)![0].ownPlace).toBeNull();   // баннер без мест
});

test("student_wakes_place_then_chat", async () => {
  const a = api({ me: student, workplace: [[200, { state: "sleeping" }], [200, { state: "ready" }]],
                  wake: [202, { state: "starting" }] });
  const polling = vi.fn(okPolling);
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={polling} pollMs={POLL_MS} />);
  expect(await screen.findByText("Готовлю рабочее место…")).toBeTruthy();
  expect(await screen.findByTestId("fake-chat")).toBeTruthy();
  expect(a.wake).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Вы вошли как s")).toBeTruthy();
  expect(screen.getByRole("note").textContent).toBe(STAND_WARNING);
  await waitFor(() => expect(polling).toHaveBeenCalled());
  expect(polling.mock.calls.at(-1)![0].ownPlace).toBe("03");  // баннер — по своему месту
});

test("banner_text_and_details_link", async () => {
  render(<App api={api({ me: student, workplace: [200, { state: "ready" }] })} navigate={vi.fn()} ChatImpl={FakeChat}
              startPolling={okPolling} />);
  await screen.findByTestId("fake-chat");
  await screen.findByText("Стенд работает");                     // вид приходит из эффекта шапки
  const banner = screen.getByRole("status");
  expect(banner.getAttribute("data-level")).toBe("ok");
  expect(banner.textContent).toContain("Стенд работает");
  const link = screen.getByRole("link", { name: "Подробнее" });
  expect(link.getAttribute("href")).toBe("/status");
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
});

test("no_header_while_loading", () => {
  const a = api({ me: student });
  a.me.mockImplementation(() => new Promise<Reply>(() => {}));   // /me не отвечает
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
  expect(screen.getByText("Чат загружается…")).toBeTruthy();
  expect(screen.queryByRole("note")).toBeNull();
  expect(screen.queryByRole("button", { name: "Выйти" })).toBeNull();
});

test("wake_failure_shows_text_and_retry", async () => {
  const a = api({ me: student, workplace: [[200, { state: "sleeping" }], [200, { state: "ready" }]],
                  wake: [[200, { state: "failed", detail: "нет памяти" }], [202, { state: "starting" }]] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} pollMs={POLL_MS} />);
  expect(await screen.findByText("Не удалось подготовить рабочее место: нет памяти")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(await screen.findByTestId("fake-chat")).toBeTruthy();
  expect(a.wake).toHaveBeenCalledTimes(2);
});

test("wake_signed_out_goes_to_login", async () => {
  const navigate = vi.fn();
  const a = api({ me: student, workplace: [200, { state: "sleeping" }], wake: [401, { name: "Unauthorized" }] });
  render(<App api={a} navigate={navigate} ChatImpl={FakeChat} startPolling={okPolling} pollMs={POLL_MS} />);
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
  expect(screen.queryByTestId("fake-chat")).toBeNull();
});

// FakeChat, которым управляет тест: на каждом монтировании пишет initialSessionId, по кнопкам зовёт колбэки.
function controlledChat() {
  const mounts: (string | undefined)[] = [];
  const Chat = (p: ChatProps) => {
    useEffect(() => { mounts.push(p.initialSessionId); }, []);   // только на монтировании
    return (
      <div data-testid="fake-chat">
        <button type="button" onClick={() => p.onThreadIdChange?.("ses_0123456789abcdefghijklmnA")}>thread</button>
        <button type="button" onClick={() => p.onHalt("asleep")}>asleep</button>
        <button type="button" onClick={() => p.onHalt("signed_out")}>signed_out</button>
      </div>
    );
  };
  return { Chat, mounts };
}

test("halt_asleep_wakes_and_reopens_last_session", async () => {
  const { Chat, mounts } = controlledChat();
  const a = api({ me: student, workplace: [[200, { state: "ready" }], [200, { state: "ready" }]],
                  wake: [202, { state: "starting" }] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={Chat} startPolling={okPolling} pollMs={POLL_MS} />);
  await screen.findByTestId("fake-chat");
  await waitFor(() => expect(mounts).toEqual([undefined]));    // монтирование пишется в эффекте
  fireEvent.click(screen.getByRole("button", { name: "thread" }));
  fireEvent.click(screen.getByRole("button", { name: "asleep" }));
  expect(await screen.findByText("Рабочее место спит")).toBeTruthy();
  expect(screen.queryByTestId("fake-chat")).toBeNull();          // чат размонтирован — поток закрыт
  expect(a.wake).not.toHaveBeenCalled();                          // сам не будит (Д5-R27)
  fireEvent.click(screen.getByRole("button", { name: "Разбудить" }));
  await screen.findByTestId("fake-chat");
  expect(a.wake).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(mounts).toEqual([undefined, "ses_0123456789abcdefghijklmnA"]));
});

test("halt_signed_out_goes_to_login", async () => {
  const { Chat } = controlledChat();
  const navigate = vi.fn();
  render(<App api={api({ me: student, workplace: [200, { state: "ready" }] })} navigate={navigate} ChatImpl={Chat}
              startPolling={okPolling} />);
  await screen.findByTestId("fake-chat");
  fireEvent.click(screen.getByRole("button", { name: "signed_out" }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
  expect(screen.queryByTestId("fake-chat")).toBeNull();
});

test("logout_posts_then_navigates", async () => {
  const navigate = vi.fn();
  const a = api({ me: student, workplace: [200, { state: "ready" }] });
  let answer: (r: Reply) => void = () => {};
  a.logout.mockImplementation(() => new Promise<Reply>((resolve) => { answer = resolve; }));
  render(<App api={a} navigate={navigate} ChatImpl={FakeChat} startPolling={okPolling} />);
  await screen.findByTestId("fake-chat");
  fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
  await waitFor(() => expect(a.logout).toHaveBeenCalledTimes(1));
  expect(screen.queryByTestId("fake-chat")).toBeNull();           // поток закрыт до выхода
  expect(navigate).not.toHaveBeenCalled();                        // сначала ответ шлюза
  answer({ status: 200, body: { ok: true } });
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
  expect(navigate).toHaveBeenCalledTimes(1);
});

test("logout_navigates_even_if_gateway_fails", async () => {
  const navigate = vi.fn();
  const a = api({ me: student, workplace: [200, { state: "ready" }], logout: [0, null] });
  render(<App api={a} navigate={navigate} ChatImpl={FakeChat} startPolling={okPolling} />);
  await screen.findByTestId("fake-chat");
  fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
});

test("logout_while_waking_stops_polling", async () => {
  const a = api({ me: student, workplace: [200, { state: "starting" }], wake: [202, { state: "starting" }] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} pollMs={POLL_MS} />);
  await waitFor(() => expect(a.workplace.mock.calls.length).toBeGreaterThanOrEqual(3));   // первый вопрос и опрос wakeAndWait
  fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
  await waitFor(() => expect(a.logout).toHaveBeenCalled());
  const polled = a.workplace.mock.calls.length;
  await new Promise((r) => setTimeout(r, POLL_MS * 8));
  expect(a.workplace.mock.calls.length).toBe(polled);            // экран места снят — ожидание отменено
});

test("banner_polling_stops_on_unmount", async () => {
  const stop = vi.fn();
  const polling = vi.fn<typeof startStatusPolling>((o) => {
    o.onView({ level: "ok", text: "Стенд работает" });
    return stop;
  });
  const { unmount } = render(<App api={api({ me: student, workplace: [200, { state: "ready" }] })} navigate={vi.fn()}
                                  ChatImpl={FakeChat} startPolling={polling} />);
  await screen.findByText("Стенд работает");
  expect(stop).not.toHaveBeenCalled();
  unmount();
  expect(stop).toHaveBeenCalledTimes(polling.mock.calls.length);  // каждый запуск опроса остановлен
});

// --- предупреждение при первом входе (Д6-R49, Review Focus 1 Д6б) ---

const NOTICE_TEXT = /Преподаватель видит ваши разговоры/;

test("notice_blocks_chat_until_ack", async () => {
  const a = api({ me: [[200, { login: "s", role: "student", place: "03", notice: "teacher-reads-1" }]], ackNotice: [204, null],
                  workplace: [[200, { state: "ready" }]] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
  expect(await screen.findByText(NOTICE_TEXT)).toBeTruthy();
  expect(a.wake).not.toHaveBeenCalled(); expect(a.workplace).not.toHaveBeenCalled(); expect(screen.queryByTestId("fake-chat")).toBeNull();
  expect(screen.getByText("Вы вошли как s")).toBeTruthy();                       // шапка с «Выйти» — и до подтверждения
  fireEvent.click(screen.getByRole("button", { name: "Понятно" }));
  await screen.findByTestId("fake-chat"); expect(a.ackNotice).toHaveBeenCalledWith("teacher-reads-1");
  expect(a.ackNotice).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(NOTICE_TEXT)).toBeNull();
});

test("old_core_without_notice_field_goes_on", async () => {                      // раунд 2, Minor 5
  const a = api({ me: [[200, { login: "s", role: "student", place: "03" }]], workplace: [[200, { state: "ready" }]] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
  await screen.findByTestId("fake-chat"); expect(a.ackNotice).not.toHaveBeenCalled();
  const b = api({ me: [[200, { login: "s", role: "student", place: "03", notice: null }]], workplace: [[200, { state: "ready" }]] });
  render(<App api={b} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
  await waitFor(() => expect(screen.getAllByTestId("fake-chat")).toHaveLength(2));   // подтверждено — сразу дальше
  expect(b.ackNotice).not.toHaveBeenCalled();
});

test("notice_ack_failure_keeps_screen", async () => {                            // раунд 2, Minor 5: 404 старого core
  const a = api({ me: [[200, { login: "s", role: "student", place: "03", notice: "teacher-reads-1" }]],
                  ackNotice: [[404, { name: "NotFound", data: { message: "Не найдено" } }], [0, null], [204, null]],
                  workplace: [[200, { state: "sleeping" }], [200, { state: "ready" }]], wake: [202, { state: "starting" }] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} pollMs={POLL_MS} />);
  await screen.findByText(NOTICE_TEXT);
  fireEvent.click(screen.getByRole("button", { name: "Понятно" }));
  expect(await screen.findByText("Не найдено")).toBeTruthy();                    // экран остаётся с текстом ошибки
  expect(screen.getByText(NOTICE_TEXT)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Понятно" }));
  expect(await screen.findByText("Серверный слой не отвечает")).toBeTruthy();    // сеть
  expect(a.wake).not.toHaveBeenCalled(); expect(a.workplace).not.toHaveBeenCalled(); expect(screen.queryByTestId("fake-chat")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Понятно" }));
  expect(await screen.findByTestId("fake-chat")).toBeTruthy();                   // подтвердили — будим место, потом чат
  expect(a.ackNotice).toHaveBeenCalledTimes(3); expect(a.wake).toHaveBeenCalledTimes(1);
});

test("notice_ack_signed_out_goes_to_login", async () => {
  const navigate = vi.fn();
  const a = api({ me: [[200, { login: "s", role: "student", place: "03", notice: "teacher-reads-1" }]],
                  ackNotice: [401, { name: "Unauthorized", data: { message: "Нужно войти" } }] });
  render(<App api={a} navigate={navigate} ChatImpl={FakeChat} startPolling={okPolling} />);
  await screen.findByText(NOTICE_TEXT);
  fireEvent.click(screen.getByRole("button", { name: "Понятно" }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
  expect(a.workplace).not.toHaveBeenCalled();
});

test("unknown_notice_is_not_acked", async () => {                                // preflight Ф14: номер без текста не подтверждается
  for (const notice of ["teacher-reads-2", "toString", 7]) {
    const a = api({ me: [[200, { login: "s", role: "student", place: "03", notice }]] });
    const { unmount } = render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
    expect(await screen.findByText(T_NOTICE_UNKNOWN)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Понятно" })).toBeNull();
    expect(screen.queryByText(NOTICE_TEXT)).toBeNull();
    expect(a.ackNotice).not.toHaveBeenCalled(); expect(a.wake).not.toHaveBeenCalled(); expect(a.workplace).not.toHaveBeenCalled();
    unmount();
  }
});

test("logout_from_notice_does_not_open_chat", async () => {                       // «Выйти», пока подтверждение в пути
  const navigate = vi.fn();
  const a = api({ me: [[200, { login: "s", role: "student", place: "03", notice: "teacher-reads-1" }]],
                  workplace: [[200, { state: "ready" }]] });
  let answer: (r: Reply) => void = () => {};
  a.ackNotice.mockImplementation(() => new Promise<Reply>((resolve) => { answer = resolve; }));
  render(<App api={a} navigate={navigate} ChatImpl={FakeChat} startPolling={okPolling} />);
  await screen.findByText(NOTICE_TEXT);
  fireEvent.click(screen.getByRole("button", { name: "Понятно" }));
  await waitFor(() => expect(a.ackNotice).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
  answer({ status: 204, body: "" });
  await new Promise((r) => setTimeout(r, 50));
  expect(a.workplace).not.toHaveBeenCalled(); expect(screen.queryByTestId("fake-chat")).toBeNull();
});

test("notice_ack_button_disabled_while_in_flight", async () => {                  // итоговая доводка Д6б, Б2-m1
  // пока подтверждение в пути, «Понятно» выключена: второе нажатие не уходит; отказ — кнопка снова доступна
  const a = api({ me: [[200, { login: "s", role: "student", place: "03", notice: "teacher-reads-1" }]],
                  workplace: [[200, { state: "ready" }]] });
  const answers: ((r: Reply) => void)[] = [];
  a.ackNotice.mockImplementation(() => new Promise<Reply>((resolve) => { answers.push(resolve); }));
  render(<App api={a} navigate={vi.fn()} ChatImpl={FakeChat} startPolling={okPolling} />);
  await screen.findByText(NOTICE_TEXT);
  const button = screen.getByRole("button", { name: "Понятно" }) as HTMLButtonElement;
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  await waitFor(() => expect(button.disabled).toBe(true));
  fireEvent.click(button);
  await new Promise((r) => setTimeout(r, 50));
  expect(a.ackNotice).toHaveBeenCalledTimes(1);                                  // второе не ушло
  answers[0]!({ status: 503, body: { name: "Unavailable", data: { message: "Учётные записи недоступны — повторите позже" } } });
  expect(await screen.findByText("Учётные записи недоступны — повторите позже")).toBeTruthy();
  expect(button.disabled).toBe(false);                                            // отказ — можно нажать снова
  fireEvent.click(button);
  await waitFor(() => expect(a.ackNotice).toHaveBeenCalledTimes(2));
  expect(button.disabled).toBe(true);
  answers[1]!({ status: 204, body: "" });
  expect(await screen.findByTestId("fake-chat")).toBeTruthy();
});

// --- Д7б: список агентов (Д7-R65, preflight П10, П11) ---

test("chat_gets_agents_loader", async () => {
  // App отдаёт чату загрузчик списка — api.agents (GET /pcbk/api/agents); сам App его не зовёт
  let loader: ChatProps["loadAgents"];
  const Spy = (p: ChatProps) => {
    loader = p.loadAgents;
    return <div data-testid="fake-chat" />;
  };
  const list = { agents: [], own: 0, limit: 20 };
  const a = api({ me: student, workplace: [200, { state: "ready" }], agents: [200, list] });
  render(<App api={a} navigate={vi.fn()} ChatImpl={Spy} startPolling={okPolling} />);
  await screen.findByTestId("fake-chat");
  expect(a.agents).not.toHaveBeenCalled();
  expect(await loader!()).toEqual({ status: 200, body: list });
  expect(a.agents).toHaveBeenCalledTimes(1);
});
