// Вкладка «События» (Д13, DESIGN п.7): загрузка при открытии, кнопка «Обновить» без опроса по таймеру, пустой
// список, таблица новыми сверху (порядок — как отдал шлюз), отказ ручки тем же способом, что у калькуляторов
// (replyText, 401 — onSignedOut), поля — только текстом.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Api, Reply } from "../src/api";
import { EventsPage } from "../src/components/EventsPage";
import { T_EVENTS_EMPTY, T_UNKNOWN } from "../src/texts";

type Answer = [number, unknown];

function eventsApi(answers: Answer | Answer[]) {
  const list = Array.isArray(answers[0]) ? (answers as Answer[]) : [answers as Answer];
  let i = 0;
  const events = vi.fn(async () => {
    const [status, body] = list[Math.min(i, list.length - 1)]!;
    i += 1;
    return { status, body } as Reply;
  });
  return { me: vi.fn(), logout: vi.fn(), wake: vi.fn(), workplace: vi.fn(), ackNotice: vi.fn(), agents: vi.fn(),
           calc: vi.fn(), report: vi.fn(async () => {}), events } as unknown as Api & { events: typeof events };
}

const E1 = { id: 2, ts: "2026-10-12T10:15:30+00:00", agent: "napusk-03", agent_title: "Напуск: напорные ящики",
             kind: "deviation", title: "Давление в НЯ ОС выше задания", tags: ["21FAKE_06_PV", "21FAKE_06_SP"],
             period_start: "2026-10-12T06:00:00+05:00", period_end: "2026-10-12T06:40:00+05:00",
             value: 1.05, setpoint: 0.94, text: "Факт превысил задание на 0,11 бар." };
const E2 = { id: 1, ts: "2026-10-12T09:00:00+00:00", agent: "napusk-03", agent_title: "Напуск: напорные ящики",
             kind: "note", title: "Проверка завершена без отклонений", tags: ["21FAKE_06_PV"],
             period_start: null, period_end: null, value: null, setpoint: null, text: "За смену отклонений не найдено." };

test("events_page_loads_on_open_without_polling", async () => {
  const api = eventsApi([200, { events: [E1, E2], limit: 100 }]);
  render(<EventsPage api={api} onSignedOut={vi.fn()} />);
  await screen.findByText("Давление в НЯ ОС выше задания");
  expect(api.events).toHaveBeenCalledTimes(1);
  // новые сверху — порядок ответа сохранён, не пересортирован
  const rows = screen.getAllByRole("row").slice(1);                               // без строки заголовков
  const first = rows[0]!;
  const second = rows[1]!;
  for (const t of ["Давление в НЯ ОС выше задания", "Отклонение от задания", "Напуск: напорные ящики",
                   "21FAKE_06_PV, 21FAKE_06_SP", "Факт превысил задание на 0,11 бар.", "1,05 / 0,94"])
    expect(first.textContent).toContain(t);
  // значение и задание отсутствуют (note) — «—», не пустая ячейка и не «undefined»
  for (const t of ["Проверка завершена без отклонений", "Заметка", "—"]) expect(second.textContent).toContain(t);
});

test("events_page_empty_text", async () => {
  const api = eventsApi([200, { events: [], limit: 100 }]);
  render(<EventsPage api={api} onSignedOut={vi.fn()} />);
  expect(await screen.findByText(T_EVENTS_EMPTY)).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

test("events_page_refresh_button_refetches_without_timer", async () => {
  const api = eventsApi([[200, { events: [], limit: 100 }], [200, { events: [E2], limit: 100 }]]);
  render(<EventsPage api={api} onSignedOut={vi.fn()} />);
  await screen.findByText(T_EVENTS_EMPTY);
  expect(api.events).toHaveBeenCalledTimes(1);                                    // без опроса по таймеру — только раз
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  await screen.findByText("Проверка завершена без отклонений");
  expect(api.events).toHaveBeenCalledTimes(2);                                    // «Обновить» — явный повторный вызов
});

test("events_page_refusal_and_signed_out", async () => {
  const onSignedOut = vi.fn();
  const api = eventsApi([[503, { name: "Unavailable", data: { message: "Служба данных недоступна" } }],
                         [401, { name: "Unauthorized" }]]);
  render(<EventsPage api={api} onSignedOut={onSignedOut} />);
  expect(await screen.findByText("Служба данных недоступна")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});

test("events_page_odd_reply_shows_fallback_text", async () => {
  const api = eventsApi([200, { odd: true }]);                                    // не по договору — не парсится
  render(<EventsPage api={api} onSignedOut={vi.fn()} />);
  expect(await screen.findByText(T_UNKNOWN)).toBeTruthy();
});
