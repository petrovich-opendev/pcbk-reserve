// Кабинет преподавателя (Д10-R106): api — заглушка TeacherApi с очередями ответов (teacherFixtures.ts, как api() в
// App.test.tsx); опрос пробуждения — pollMs.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Reply } from "../src/api";
import { TeacherPage } from "../src/components/TeacherPage";
import type { Timers } from "../src/workplace";
import { AGENTS_03, JOURNAL_ROWS, OVERVIEW_READY_03, OVERVIEW_SLEEPING_05, SESSION, SESSION_ARCHIVED, TRANSCRIPT_WITH_HTML,
         teacherApi } from "./teacherFixtures";

test("overview_then_reader_is_read_only", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], sessions: [200, { sessions: [SESSION] }],
                         session: [200, TRANSCRIPT_WITH_HTML] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /Открыть.*03/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Значение 20FAKE_001_PV/ }));
  // waitFor с колбэком, который не бросает, вернулся бы сразу с null: ждём явной проверкой (правка теста брифа)
  await waitFor(() => expect(container.querySelector("section.transcript")).not.toBeNull());
  const t = container.querySelector("section.transcript")!;
  expect(t.textContent).toContain("<img src=x onerror=alert(1)>");            // текстом, не разметкой
  expect(t.querySelector("img, textarea, input, [contenteditable]")).toBeNull();
  expect(screen.getByText("Только чтение: писать в разговор студента нельзя")).toBeTruthy();
  expect(a.shares).not.toHaveBeenCalled();
  // сверх брифа: разметка стенограммы — договор с А5; вызовы — по месту и разговору из обзора
  expect(a.sessions).toHaveBeenCalledWith("03");
  expect(a.session).toHaveBeenCalledWith("03", SESSION.id);
  expect([...t.querySelectorAll("div.msg")].map((m) => m.getAttribute("data-role"))).toEqual(["user", "assistant", "assistant"]);
  expect(t.querySelector("script, b")).toBeNull();
  expect(t.textContent).toContain("<script>alert(2)</script>");
  expect(t.textContent).toContain("Запрос к данным: текущее значение — готово");
  expect(t.textContent).toContain("Агент «Помощник технолога КДМ-1»");
  expect(t.querySelector('[data-kind="fail"]')!.textContent).toBe("Ваша доля бюджета на модель израсходована");
  expect(t.textContent).toContain("Разговор показан не целиком — он слишком длинный");
  expect(t.querySelectorAll("button")).toHaveLength(0);                         // и кнопок отправки нет
});

test("sleeping_place_wakes_by_button_and_polls", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_SLEEPING_05],
                         sessions: [[503, { name: "WorkplaceNotReady", data: { message: "Рабочее место студента спит — разбудите его для просмотра" } }],
                                    [200, { sessions: [] }]],
                         wake: [202, { place: "05", state: "starting", text: "Готовлю рабочее место…" }],
                         place: [[200, { place: "05", state: "starting", text: "…" }], [200, { place: "05", state: "ready", text: "…" }]] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} pollMs={20} />);
  fireEvent.click(await screen.findByRole("button", { name: /Открыть.*05/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Разбудить для просмотра" }));
  expect(await screen.findByText("Разговоров нет")).toBeTruthy();
  expect(a.wake).toHaveBeenCalledTimes(1);
  // сверх брифа: опрос — по месту, до «готово»; список — заново после пробуждения
  expect(a.wake).toHaveBeenCalledWith("05");
  expect(a.place).toHaveBeenCalledTimes(2);
  expect(a.place).toHaveBeenCalledWith("05");
  expect(a.sessions).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "Разбудить для просмотра" })).toBeNull();
});

test("transfer_sends_exact_body_and_shows_conflict", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03],
                         shares: [[409, { name: "Conflict", data: { message: "Доли изменились, пока страница была открыта, — обновите её" } }]] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await screen.findByRole("table");
  fireEvent.change(screen.getByLabelText("С места"), { target: { value: "03" } });
  fireEvent.change(screen.getByLabelText("На место"), { target: { value: "05" } });
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: "20" } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText(/Доли изменились/)).toBeTruthy();
  expect(a.shares).toHaveBeenCalledWith([{ place: "03", from_cents: 10000, to_cents: 8000 },
                                         { place: "05", from_cents: 10000, to_cents: 12000 }]);
  expect(a.overview).toHaveBeenCalledTimes(2);                               // обзор заново после 409
});

test("stop_place_button_calls_server_and_reloads", async () => {      // I-1, m-5 р2: пол считает сервер
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03],
                         stopPlace: [[400, { name: "BadRequest", data: { message: "Доля места 03 уже не выше оплаченного: 12,34 $" } }],
                                     [200, { places: OVERVIEW_READY_03.places, budget: OVERVIEW_READY_03.budget }]] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await screen.findByRole("table");
  fireEvent.change(screen.getByLabelText("С места"), { target: { value: "03" } });
  fireEvent.change(screen.getByLabelText("На место"), { target: { value: "05" } });
  fireEvent.click(screen.getByRole("button", { name: "Остановить место" }));
  expect(await screen.findByText(/уже не выше оплаченного/)).toBeTruthy();
  expect(a.stopPlace).toHaveBeenCalledWith("03", "05");                      // числа пола в вызове нет
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));         // на 400 — обзор заново
  fireEvent.click(screen.getByRole("button", { name: "Остановить место" }));
  expect(await screen.findByText("Доли обновлены")).toBeTruthy();
  expect(a.shares).not.toHaveBeenCalled();
});

test("signed_out_on_401", async () => {
  const onSignedOut = vi.fn();
  render(<TeacherPage api={teacherApi({ overview: [401, { name: "Unauthorized" }] })} onSignedOut={onSignedOut} />);
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});

// --- сверх брифа ---

test("overview_table_marks_budget_and_refresh", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  expect(await screen.findByText("Кабинет преподавателя")).toBeTruthy();
  const table = await screen.findByRole("table");
  expect(table.classList.contains("places")).toBe(true);
  expect([...table.querySelectorAll("tbody tr")].map((r) => r.getAttribute("data-place")))
    .toEqual(["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11"]);   // Д15б: 11 мест
  const cells = (nn: string) => [...container.querySelectorAll(`tr[data-place="${nn}"] td`)].map((c) => c.textContent!.replace(/\s/g, " "));
  expect(cells("03").slice(0, 6)).toEqual(["03", "stu-03", "работает", "100,00 $", "12,35 $", "2"]);
  expect(cells("06")[1]).toBe("stu-06 не подтвердил предупреждение");
  expect(cells("07")[1]).toBe("stu-07 отключена");
  expect(cells("08")[1]).toBe("stu-08 замок");
  expect(cells("10").slice(1, 3)).toEqual(["свободно", "спит"]);
  expect(container.querySelectorAll("button.open-place")).toHaveLength(10);   // у свободного места открывать нечего (Д15б: 11 мест)
  expect(container.querySelector('tr[data-place="10"] button.open-place')).toBeNull();
  expect(screen.getByRole("button", { name: "Открыть место 03" }).textContent).toBe("Открыть");
  expect(screen.getByText(/^Общий бюджет:/).textContent!.replace(/\s/g, " ")).toBe(
    "Общий бюджет: потрачено 12,35 $ из 1 000,00 $; распределено по местам 1 000,00 $, свободно 0,00 $; отбор и проверки 0,50 $");
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));        // сам обзор не опрашивается — только кнопкой
  await new Promise((r) => setTimeout(r, 50));
  expect(a.overview).toHaveBeenCalledTimes(2);
  expect(a.sessions).not.toHaveBeenCalled();                                  // обзор места не трогает
});

test("overview_errors_and_no_budget", async () => {
  render(<TeacherPage api={teacherApi({ overview: [200, { places: [] }] })} onSignedOut={vi.fn()} />);
  // ревью А4, m-4: кабинет — не «сообщите преподавателю»; начало текста — по R106
  expect(await screen.findByText("Неожиданный ответ сервера — сообщите администратору стенда.")).toBeTruthy();
  const none = { ...OVERVIEW_READY_03, budget: null, shares_ok: false,
                 places: OVERVIEW_READY_03.places.map((p) => ({ ...p, share_cents: null, floor_cents: null, spent_usd: null })) };
  const { container } = render(<TeacherPage api={teacherApi({ overview: [200, none] })} onSignedOut={vi.fn()} />);
  expect(await screen.findByText("Доли бюджета недоступны — перевод закрыт")).toBeTruthy();
  expect([...container.querySelectorAll('tr[data-place="03"] td')].slice(3, 5).map((c) => c.textContent)).toEqual(["—", "—"]);
  expect((container.querySelector("button.share-move") as HTMLButtonElement).disabled).toBe(true);
  expect((container.querySelector("button.share-floor") as HTMLButtonElement).disabled).toBe(true);
});

test("transfer_checks_before_sending_then_ok_reloads", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03],
                         shares: [200, { places: OVERVIEW_READY_03.places, budget: OVERVIEW_READY_03.budget }] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await screen.findByRole("table");
  expect(container.querySelector("#share-from")).toBe(screen.getByLabelText("С места"));
  expect(container.querySelector("#share-to")).toBe(screen.getByLabelText("На место"));
  expect(container.querySelector("#share-amount")).toBe(screen.getByLabelText("Сумма, $"));
  fireEvent.change(screen.getByLabelText("С места"), { target: { value: "03" } });
  fireEvent.change(screen.getByLabelText("На место"), { target: { value: "05" } });
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: "1,234" } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText("Сумма — в долларах, не больше двух знаков после запятой")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: "90" } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText("У места 03 можно взять не больше 87,66 $")).toBeTruthy();
  expect(a.shares).not.toHaveBeenCalled();                                   // подсказка — без запроса
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: "20.5" } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText("Доли обновлены")).toBeTruthy();
  expect(a.shares).toHaveBeenCalledWith([{ place: "03", from_cents: 10000, to_cents: 7950 },
                                         { place: "05", from_cents: 10000, to_cents: 12050 }]);
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));         // 200 — новый обзор
  await waitFor(() => expect(a.journal).toHaveBeenCalledTimes(2));          // и журнал
  expect(a.stopPlace).not.toHaveBeenCalled();
});

test("share_refusal_400_and_503_text_as_is", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03],
                         shares: [[400, { name: "BadRequest", data: { message: "Сумма долей мест больше общего бюджета: 1 000,01 $ из 1 000,00 $" } }],
                                  [503, { name: "Unavailable", data: { message: "Журнал действий преподавателя не пишется — просмотр и изменения закрыты" } }]] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await screen.findByRole("table");
  fireEvent.change(screen.getByLabelText("С места"), { target: { value: "03" } });
  fireEvent.change(screen.getByLabelText("На место"), { target: { value: "05" } });
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText("Сумма долей мест больше общего бюджета: 1 000,01 $ из 1 000,00 $")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText("Журнал действий преподавателя не пишется — просмотр и изменения закрыты")).toBeTruthy();
  expect(a.overview).toHaveBeenCalledTimes(1);                               // 400 и 503 перевода — только текст
});

test("agents_tab_cards_and_instruction", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], sessions: [200, { sessions: [SESSION, SESSION_ARCHIVED] }],
                         agents: [200, AGENTS_03], agent: [200, { name: "pech-1", text: "# Печь 1\n<script>x</script>" }] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть место 03" }));
  const archived = await screen.findByRole("button", { name: /Без названия/ });
  expect(archived.textContent).toContain("в архиве");
  expect(container.querySelectorAll(".sessions button[data-session-id]")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Агенты" }));
  const card = await screen.findByText("Печь 1");
  const article = card.closest("article")!;
  expect(article.textContent).toContain("Узел печей");
  expect(article.textContent).toContain("Следит за 20FAKE_001_PV");
  expect(article.textContent).toContain("текущее значение, сводка за период");   // инструменты учёта — словами
  expect(article.textContent).toContain("работает");
  fireEvent.click(within(article).getByRole("button", { name: "Показать инструкцию" }));
  await waitFor(() => expect(article.querySelector("pre")).not.toBeNull());
  const pre = article.querySelector("pre")!;
  expect(pre.textContent).toBe("# Печь 1\n<script>x</script>");
  expect(article.querySelector("script")).toBeNull();
  expect(a.agent).toHaveBeenCalledWith("03", "pech-1");
  fireEvent.click(screen.getByRole("button", { name: "Разговоры" }));      // вкладка не теряет список
  expect(container.querySelectorAll(".sessions button[data-session-id]")).toHaveLength(2);
  expect(a.sessions).toHaveBeenCalledTimes(1);
  expect(a.agents).toHaveBeenCalledTimes(1);
});

test("reader_asleep_mid_read_offers_wake_and_401_signs_out", async () => {
  const onSignedOut = vi.fn();
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], sessions: [200, { sessions: [SESSION] }],
                         session: [[503, { name: "WorkplaceNotReady", data: { message: "Рабочее место студента спит — разбудите его для просмотра" } }],
                                   [401, { name: "Unauthorized" }]],
                         wake: [202, { place: "03", state: "ready", detail: "Рабочее место готово" }] });
  render(<TeacherPage api={a} onSignedOut={onSignedOut} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть место 03" }));
  fireEvent.click(await screen.findByRole("button", { name: /Значение 20FAKE_001_PV/ }));
  expect(await screen.findByText("Рабочее место студента спит — разбудите его для просмотра")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Разбудить для просмотра" }));
  fireEvent.click(await screen.findByRole("button", { name: /Значение 20FAKE_001_PV/ }));
  await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1));
});

test("journal_lists_actions_in_words", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], journal: [200, JOURNAL_ROWS] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await waitFor(() => expect(container.querySelectorAll(".journal li")).toHaveLength(3));
  const items = [...container.querySelectorAll(".journal li")].map((li) => li.textContent!.replace(/\s/g, " "));
  expect(items[0]).toContain("перевёл долю");
  expect(items[0]).toContain("03: 100,00 $ → 80,00 $");
  expect(items[0]).toContain("выполнено");
  expect(items[1]).toContain("прочитал разговор");
  expect(items[1]).toContain("место 03");
  expect(items[1]).toContain("запрошено");
  expect(items[2]).toContain("разбудил место");
  expect(items[2]).toContain("место 05");
  fireEvent.click(screen.getByRole("button", { name: "Обновить журнал" }));
  await waitFor(() => expect(a.journal).toHaveBeenCalledTimes(2));
});

// --- итоговая доводка Д10а: Minor 1–7 ревью А4 ---

const SHARES_OK: [number, unknown] = [200, { places: OVERVIEW_READY_03.places, budget: OVERVIEW_READY_03.budget }];
const ASLEEP_05: [number, unknown] =
  [503, { name: "WorkplaceNotReady", data: { message: "Рабочее место студента спит — разбудите его для просмотра" } }];

function deferred(): { promise: Promise<Reply>; resolve(r: Reply): void } {
  let resolve!: (r: Reply) => void;
  const promise = new Promise<Reply>((r) => { resolve = r; });
  return { promise, resolve };
}

// таймеры под рукой теста: видно, какие ещё ждут (опрос и срок пробуждения)
function heldTimers(): Timers & { pending: Map<number, () => void> } {
  const pending = new Map<number, () => void>();
  let n = 0;
  return {
    pending,
    setTimeout: (fn, _ms) => { n += 1; pending.set(n, fn); return n; },
    clearTimeout: (id) => { pending.delete(id as number); },
  };
}

function transfer(amount = "1"): void {
  fireEvent.change(screen.getByLabelText("С места"), { target: { value: "03" } });
  fireEvent.change(screen.getByLabelText("На место"), { target: { value: "05" } });
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: amount } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
}

test("failed_overview_after_change_keeps_page", async () => {          // m-1: прежний обзор и разговор остаются
  const a = teacherApi({ overview: [[200, OVERVIEW_READY_03], [500, null], [200, OVERVIEW_READY_03]],
                         sessions: [200, { sessions: [SESSION] }], session: [200, TRANSCRIPT_WITH_HTML], shares: SHARES_OK });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть место 03" }));
  fireEvent.click(await screen.findByRole("button", { name: /Значение 20FAKE_001_PV/ }));
  await waitFor(() => expect(container.querySelector("section.transcript")).not.toBeNull());
  transfer();
  expect(await screen.findByText("Обзор не обновился: Серверный слой не отвечает")).toBeTruthy();
  expect(screen.getByText("Доли обновлены")).toBeTruthy();                       // удачный перевод не стёрт
  expect(container.querySelectorAll("table.places tbody tr")).toHaveLength(11);  // Д15б: 11 мест
  expect(container.querySelector("section.transcript")).not.toBeNull();          // и открытый разговор
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  await waitFor(() => expect(screen.queryByText(/^Обзор не обновился/)).toBeNull());   // новый обзор — строки нет
  expect(a.overview).toHaveBeenCalledTimes(3);
});

test("journal_503_on_list_is_text_without_wake", async () => {         // m-2: «спит» — только WorkplaceNotReady
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03],
                         sessions: [503, { name: "Unavailable", data: { message: "Журнал действий преподавателя не пишется — просмотр и изменения закрыты" } }] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть место 03" }));
  expect(await screen.findByText("Журнал действий преподавателя не пишется — просмотр и изменения закрыты")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Разбудить для просмотра" })).toBeNull();
  expect(screen.getByRole("button", { name: "Обновить список" })).toBeTruthy();
  expect(a.wake).not.toHaveBeenCalled();
});

test("leaving_during_wake_clears_both_timers", async () => {           // m-3: опрос не утекает таймерами
  const timers = heldTimers();
  const a = teacherApi({ overview: [200, OVERVIEW_SLEEPING_05], sessions: ASLEEP_05,
                         wake: [202, { place: "05", state: "starting", detail: "…" }],
                         place: [200, { place: "05", state: "starting", detail: "…" }] });
  const { unmount } = render(<TeacherPage api={a} onSignedOut={vi.fn()} timers={timers} />);
  fireEvent.click(await screen.findByRole("button", { name: /Открыть.*05/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Разбудить для просмотра" }));
  await waitFor(() => expect(timers.pending.size).toBe(2));                // срок 90 с и следующий опрос
  unmount();
  expect(timers.pending.size).toBe(0);
  expect(a.place).not.toHaveBeenCalled();
});

test("failed_wake_offers_wake_again", async () => {                    // m-3: кнопка повтора после неудачи
  const a = teacherApi({ overview: [200, OVERVIEW_SLEEPING_05], sessions: [ASLEEP_05, [200, { sessions: [] }]],
                         wake: [[202, { place: "05", state: "failed", detail: "нет памяти" }],
                                [202, { place: "05", state: "ready", detail: "Рабочее место готово" }]] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} pollMs={20} />);
  fireEvent.click(await screen.findByRole("button", { name: /Открыть.*05/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Разбудить для просмотра" }));
  expect(await screen.findByText("Не удалось подготовить рабочее место: нет памяти")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Разбудить для просмотра" }));
  expect(await screen.findByText("Разговоров нет")).toBeTruthy();
  expect(a.wake).toHaveBeenCalledTimes(2);
});

test("teacher_texts_for_place_and_unknown", async () => {             // m-4: «сообщите администратору стенда»
  const a = teacherApi({ overview: [200, OVERVIEW_SLEEPING_05], sessions: ASLEEP_05,
                         wake: [202, { place: "05", state: "weird" }], agents: [418, { odd: true }] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} pollMs={20} />);
  fireEvent.click(await screen.findByRole("button", { name: /Открыть.*05/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Разбудить для просмотра" }));
  expect(await screen.findByText("Непонятный ответ о рабочем месте — повторите или сообщите администратору стенда."))
    .toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Агенты" }));
  expect(await screen.findByText("Неожиданный ответ сервера — сообщите администратору стенда.")).toBeTruthy();
  expect(screen.queryByText(/сообщите преподавателю/)).toBeNull();
});

test("wake_gave_up_text_is_for_teacher", async () => {                 // m-4: срок пробуждения — кабинетным текстом
  const timers = heldTimers();
  const a = teacherApi({ overview: [200, OVERVIEW_SLEEPING_05], sessions: ASLEEP_05,
                         wake: [202, { place: "05", state: "starting" }], place: [200, { place: "05", state: "starting" }] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} timers={timers} />);
  fireEvent.click(await screen.findByRole("button", { name: /Открыть.*05/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Разбудить для просмотра" }));
  await waitFor(() => expect(timers.pending.size).toBe(2));
  act(() => { timers.pending.get(Math.min(...timers.pending.keys()))!(); });  // первым поставлен срок 90 с
  expect(await screen.findByText(
    "Рабочее место не ответило за полторы минуты — повторите или сообщите администратору стенда.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Разбудить для просмотра" })).toBeTruthy();
});

test("every_handle_401_signs_out_once", async () => {                  // m-3: 401 любой ручки и защёлка входа
  const cases: [string, Parameters<typeof teacherApi>[0], (() => void) | null][] = [
    ["sessions", { sessions: [401, null] }, () => fireEvent.click(screen.getByRole("button", { name: "Открыть место 03" }))],
    ["agents", { sessions: [200, { sessions: [] }], agents: [401, null] }, () => {
      fireEvent.click(screen.getByRole("button", { name: "Открыть место 03" }));
      fireEvent.click(screen.getByRole("button", { name: "Агенты" }));
    }],
    ["journal", { journal: [401, null] }, null],
    ["shares", { shares: [401, null] }, () => transfer()],
    ["stopPlace", { stopPlace: [401, null] }, () => {
      fireEvent.change(screen.getByLabelText("С места"), { target: { value: "03" } });
      fireEvent.change(screen.getByLabelText("На место"), { target: { value: "05" } });
      fireEvent.click(screen.getByRole("button", { name: "Остановить место" }));
    }],
  ];
  for (const [name, answers, act_] of cases) {
    const onSignedOut = vi.fn();
    const { unmount } = render(<TeacherPage api={teacherApi({ overview: [200, OVERVIEW_READY_03], ...answers })}
                                            onSignedOut={onSignedOut} />);
    await screen.findByRole("table");
    act_?.();
    await waitFor(() => expect(onSignedOut, name).toHaveBeenCalledTimes(1));
    unmount();
  }
  // агент: инструкция 401
  const onSignedOut = vi.fn();
  render(<TeacherPage api={teacherApi({ overview: [200, OVERVIEW_READY_03], sessions: [200, { sessions: [] }],
                                        agents: [200, AGENTS_03], agent: [401, null] })} onSignedOut={onSignedOut} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть место 03" }));
  fireEvent.click(screen.getByRole("button", { name: "Агенты" }));
  fireEvent.click(await screen.findByRole("button", { name: "Показать инструкцию" }));
  await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1));
});

test("sign_out_latch_calls_once_for_two_401", async () => {            // m-3: защёлка signOut
  const onSignedOut = vi.fn();
  render(<TeacherPage api={teacherApi({ overview: [200, OVERVIEW_READY_03], journal: [401, null], shares: [401, null] })}
                      onSignedOut={onSignedOut} />);
  await screen.findByRole("table");
  await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1));     // журнал при открытии — 401
  transfer();                                                             // и перевод — 401
  await waitFor(() => expect(screen.getByRole("button", { name: "Перевести" }).hasAttribute("disabled")).toBe(false));
  expect(onSignedOut).toHaveBeenCalledTimes(1);
});

test("late_overview_answer_does_not_overwrite_newer", async () => {     // m-3: номер запроса обзора
  const a = teacherApi();
  const late = deferred();
  const fresh = { ...OVERVIEW_READY_03,
                  places: OVERVIEW_READY_03.places.map((p) => (p.place === "03" ? { ...p, agents: 7 } : p)) };
  a.overview.mockReturnValueOnce(late.promise).mockResolvedValue({ status: 200, body: fresh });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));      // второй обзор, пока первый в пути
  await waitFor(() => expect(container.querySelector('tr[data-place="03"] td:nth-child(6)')?.textContent).toBe("7"));
  await act(async () => { late.resolve({ status: 200, body: OVERVIEW_READY_03 }); });   // старый ответ — поздно
  expect(container.querySelector('tr[data-place="03"] td:nth-child(6)')!.textContent).toBe("7");
});

test("late_list_answer_does_not_overwrite_asleep", async () => {        // m-3: номер запроса списка разговоров
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03] });
  const reading = deferred();
  const listing = deferred();
  a.sessions.mockResolvedValueOnce({ status: 200, body: { sessions: [SESSION] } }).mockReturnValueOnce(listing.promise);
  a.session.mockReturnValueOnce(reading.promise);
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть место 03" }));
  fireEvent.click(await screen.findByRole("button", { name: /Значение 20FAKE_001_PV/ }));   // чтение в пути
  fireEvent.click(screen.getByRole("button", { name: "Обновить список" }));              // и список в пути
  await act(async () => { reading.resolve({ status: ASLEEP_05[0], body: ASLEEP_05[1] }); });   // место уснуло
  expect(await screen.findByRole("button", { name: "Разбудить для просмотра" })).toBeTruthy();
  await act(async () => { listing.resolve({ status: 200, body: { sessions: [SESSION] } }); });   // старый список
  expect(screen.getByRole("button", { name: "Разбудить для просмотра" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Значение 20FAKE_001_PV/ })).toBeNull();
});

test("shares_select_names_unbound_place_like_table", async () => {      // m-5: «без учётки», а не «свободно»
  const gone = { ...OVERVIEW_READY_03, places: OVERVIEW_READY_03.places.map((p) => (p.place === "09"
    ? { ...p, login: null, free: false, disabled: null, noticed: null, locked: null } : p)) };
  const { container } = render(<TeacherPage api={teacherApi({ overview: [200, gone] })} onSignedOut={vi.fn()} />);
  await screen.findByRole("table");
  const option = (nn: string) => container.querySelector(`#share-from option[value="${nn}"]`)!.textContent;
  expect([option("09"), option("10")]).toEqual(["09 — без учётки", "10 — свободно"]);
  expect([...container.querySelectorAll('tr[data-place="09"] td')][1]!.textContent).toBe("без учётки");
});

test("labels_take_only_own_keys", async () => {                         // m-6: Object.hasOwn у подписей
  const odd = { ...OVERVIEW_READY_03,
                places: OVERVIEW_READY_03.places.map((p) => (p.place === "03" ? { ...p, state: "constructor" } : p)) };
  const journal = { rows: [{ id: 1, at: "2026-10-11T09:00:00+00:00", login: "t", action: "toString", place: null,
                             target: null, outcome: "constructor" }] };
  const agents = { agents: [{ ...AGENTS_03.agents[0]!, state: "valueOf" }] };
  const a = teacherApi({ overview: [200, odd], journal: [200, journal], sessions: [200, { sessions: [] }],
                         agents: [200, agents] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await screen.findByRole("table");
  expect(container.querySelector('tr[data-place="03"] td[data-state]')!.textContent).toBe("constructor");
  await waitFor(() => expect(container.querySelectorAll(".journal li")).toHaveLength(1));
  const li = container.querySelector(".journal li")!;
  expect([li.querySelector(".journal-what")!.textContent, li.querySelector(".journal-outcome")!.textContent])
    .toEqual(["toString", "constructor"]);
  fireEvent.click(screen.getByRole("button", { name: "Открыть место 03" }));
  fireEvent.click(screen.getByRole("button", { name: "Агенты" }));
  const card = (await screen.findByText("Печь 1")).closest("article")!;
  expect([...card.querySelectorAll("dd")].map((d) => d.textContent)[4]).toBe("valueOf");   // строка «Состояние»
});

test("agent_text_error_offers_show_again", async () => {               // m-7: «Скрыть» — только у показанной
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], sessions: [200, { sessions: [] }], agents: [200, AGENTS_03],
                         agent: [[503, { name: "Unavailable", data: { message: "Учёт агентов недоступен — повторите позже" } }],
                                 [200, { name: "pech-1", text: "# Печь 1" }]] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть место 03" }));
  fireEvent.click(screen.getByRole("button", { name: "Агенты" }));
  const article = (await screen.findByText("Печь 1")).closest("article")!;
  fireEvent.click(within(article).getByRole("button", { name: "Показать инструкцию" }));
  expect(await within(article).findByText("Учёт агентов недоступен — повторите позже")).toBeTruthy();
  expect(within(article).queryByRole("button", { name: "Скрыть инструкцию" })).toBeNull();
  fireEvent.click(within(article).getByRole("button", { name: "Показать инструкцию" }));   // повтор
  await waitFor(() => expect(article.querySelector("pre")?.textContent).toBe("# Печь 1"));
  fireEvent.click(within(article).getByRole("button", { name: "Скрыть инструкцию" }));
  expect(article.querySelector("pre")).toBeNull();
  expect(a.agent).toHaveBeenCalledTimes(2);
});
