// Вкладка «Уроки» (Д15а, DESIGN): список из индекса, первый урок открыт сам, клик — другой урок; пустой индекс,
// отказ ручки и сеть — один текст отказа; 401 — onSignedOut. Markdown урока (LessonMarkdown): тот же безопасный
// Link, что в чате (MessageParts.tsx), картинки — только свои (img/<имя>), прочее — текстом, сырой HTML — текстом.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Api, Reply } from "../src/api";
import { LessonMarkdown } from "../src/components/LessonMarkdown";
import { LessonsPage } from "../src/components/LessonsPage";
import { T_LESSONS_EMPTY, T_LESSONS_ERROR } from "../src/texts";

type Answer = [number, unknown];

// api.lessons() — очередь ответов (как eventsApi в EventsPage.test.tsx); api.lesson(id) — по карте id → ответ,
// без записи в карту — запасной ответ (по умолчанию — не найден).
function lessonsApi(indexAnswers: Answer | Answer[], byId: Record<string, Answer> = {}) {
  const list = Array.isArray(indexAnswers[0]) ? (indexAnswers as Answer[]) : [indexAnswers as Answer];
  let i = 0;
  const lessons = vi.fn(async () => {
    const [status, body] = list[Math.min(i, list.length - 1)]!;
    i += 1;
    return { status, body } as Reply;
  });
  const lesson = vi.fn(async (id: string) => {
    const [status, body] = byId[id] ?? [404, { name: "NotFound" }];
    return { status, body } as Reply;
  });
  return { me: vi.fn(), logout: vi.fn(), wake: vi.fn(), workplace: vi.fn(), ackNotice: vi.fn(), agents: vi.fn(),
           calc: vi.fn(), report: vi.fn(async () => {}), lessons, lesson } as unknown as
           Api & { lessons: typeof lessons; lesson: typeof lesson };
}

const M1 = { id: "01-vhod", title: "Вход в стенд", summary: "Как войти и что увидите первым делом" };
const M2 = { id: "02-pomoshnik", title: "Помощник", summary: "Как спросить агента о теге" };

test("lessons_page_lists_opens_first_and_switches", async () => {
  const api = lessonsApi([200, { lessons: [M1, M2] }], {
    "01-vhod": [200, { id: "01-vhod", title: "Вход в стенд", markdown: "Текст первого урока." }],
    "02-pomoshnik": [200, { id: "02-pomoshnik", title: "Помощник", markdown: "Текст второго урока." }],
  });
  render(<LessonsPage api={api} onSignedOut={vi.fn()} />);
  // первый урок списка открывается сам, без клика
  await screen.findByText("Текст первого урока.");
  expect(api.lesson).toHaveBeenCalledTimes(1);
  expect(api.lesson).toHaveBeenCalledWith("01-vhod");
  for (const t of [M1.title, M2.title]) expect(screen.getByRole("button", { name: new RegExp(t) })).toBeTruthy();
  const first = screen.getByRole("button", { name: new RegExp(M1.title) });
  expect(first.getAttribute("aria-pressed")).toBe("true");

  // клик на второй урок — другой текст, другой вызов
  fireEvent.click(screen.getByRole("button", { name: new RegExp(M2.title) }));
  await screen.findByText("Текст второго урока.");
  expect(screen.queryByText("Текст первого урока.")).toBeNull();
  expect(api.lesson).toHaveBeenCalledTimes(2);
  expect(api.lesson).toHaveBeenLastCalledWith("02-pomoshnik");
});

test("lessons_page_empty_index", async () => {
  const api = lessonsApi([200, { lessons: [] }]);
  render(<LessonsPage api={api} onSignedOut={vi.fn()} />);
  expect(await screen.findByText(T_LESSONS_EMPTY)).toBeTruthy();
  expect(api.lesson).not.toHaveBeenCalled();
});

test("lessons_page_index_refusal_shows_generic_error", async () => {
  const api = lessonsApi([503, { name: "Unavailable", data: { message: "Пакет уроков не читается" } }]);
  render(<LessonsPage api={api} onSignedOut={vi.fn()} />);
  expect(await screen.findByText(T_LESSONS_ERROR)).toBeTruthy();
});

test("lessons_page_index_network_failure_shows_generic_error", async () => {
  const api = { me: vi.fn(), logout: vi.fn(), wake: vi.fn(), workplace: vi.fn(), ackNotice: vi.fn(), agents: vi.fn(),
                calc: vi.fn(), report: vi.fn(async () => {}),
                lessons: vi.fn(async () => { throw new Error("сеть оборвалась"); }),
                lesson: vi.fn() } as unknown as Api;
  render(<LessonsPage api={api} onSignedOut={vi.fn()} />);
  expect(await screen.findByText(T_LESSONS_ERROR)).toBeTruthy();
});

test("lessons_page_index_signed_out", async () => {
  const onSignedOut = vi.fn();
  const api = lessonsApi([401, { name: "Unauthorized" }]);
  render(<LessonsPage api={api} onSignedOut={onSignedOut} />);
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});

test("lessons_page_lesson_signed_out", async () => {
  const onSignedOut = vi.fn();
  const api = lessonsApi([200, { lessons: [M1] }], { "01-vhod": [401, { name: "Unauthorized" }] });
  render(<LessonsPage api={api} onSignedOut={onSignedOut} />);
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});

// --- markdown урока (LessonMarkdown): та же безопасная ссылка и сырой HTML, что у чата; картинки — только свои ---

test("lesson_markdown_own_image_becomes_img_tag", () => {
  render(<LessonMarkdown markdown="![a](img/x.png)" />);
  const img = screen.getByRole("img", { name: "a" });
  expect(img.getAttribute("src")).toBe("/pcbk/api/lessons/img/x.png");
  expect(img.getAttribute("loading")).toBe("lazy");
});

test("lesson_markdown_external_and_traversal_images_are_text", () => {
  render(<LessonMarkdown markdown="![b](https://e.x/y.png)" />);
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText("[картинка: b]")).toBeTruthy();
  const { unmount } = render(<LessonMarkdown markdown="![c](../y.png)" />);
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText("[картинка: c]")).toBeTruthy();
  unmount();
});

test("lesson_markdown_javascript_link_is_inert", () => {
  render(<LessonMarkdown markdown="[s](javascript:alert(1))" />);
  const link = screen.getByText("s");
  expect(link.tagName).toBe("A");
  expect(link.hasAttribute("href")).toBe(false);
  expect(screen.queryByRole("link")).toBeNull();
});

test("lesson_markdown_raw_html_shown_as_text", () => {
  const { container } = render(<LessonMarkdown markdown={"до <script>alert(1)</script> после"} />);
  expect(container.textContent).toContain("<script>alert(1)</script>");
});
