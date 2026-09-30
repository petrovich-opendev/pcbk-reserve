// Фазы чата (Д6-R39, R42, R44): вход, преподаватель, пробуждение места, «место спит», выход.
// api и ChatImpl подменены; startPolling — заглушка, отдающая вид «ok»; опрос места — pollMs (M11).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { expect, test, vi } from "vitest";
import { App } from "../src/App";
import type { Api, Reply } from "../src/api";
import type { ChatProps } from "../src/components/Chat";
import type { startStatusPolling } from "../src/status";
import { STAND_WARNING } from "../src/texts";

type Answer = [number, unknown];
type Answers = Answer | Answer[];

// Ответы по ручкам: один ответ — на каждый вызов, список — по очереди (последний повторяется).
function api(o: { me?: Answers; workplace?: Answers; wake?: Answers; logout?: Answers }) {
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
  expect(polling.mock.calls.at(-1)![0].ownPlace).toBe("03");  // баннер — по своему месту
});

test("banner_text_and_details_link", async () => {
  render(<App api={api({ me: student, workplace: [200, { state: "ready" }] })} navigate={vi.fn()} ChatImpl={FakeChat}
              startPolling={okPolling} />);
  await screen.findByTestId("fake-chat");
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
  expect(mounts).toEqual([undefined]);
  fireEvent.click(screen.getByRole("button", { name: "thread" }));
  fireEvent.click(screen.getByRole("button", { name: "asleep" }));
  expect(await screen.findByText("Рабочее место спит")).toBeTruthy();
  expect(screen.queryByTestId("fake-chat")).toBeNull();          // чат размонтирован — поток закрыт
  expect(a.wake).not.toHaveBeenCalled();                          // сам не будит (Д5-R27)
  fireEvent.click(screen.getByRole("button", { name: "Разбудить" }));
  await screen.findByTestId("fake-chat");
  expect(a.wake).toHaveBeenCalledTimes(1);
  expect(mounts).toEqual([undefined, "ses_0123456789abcdefghijklmnA"]);
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
